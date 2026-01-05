"""Notification system for alerting on suspicious trades."""

import asyncio
import platform
from datetime import datetime
from typing import Optional
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

from config import settings
from detector import DetectionResult, Severity

logger = logging.getLogger(__name__)

# Optional imports for phone notifications
try:
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False

try:
    import aiosmtplib
    AIOSMTPLIB_AVAILABLE = True
except ImportError:
    AIOSMTPLIB_AVAILABLE = False

# Store for in-app notifications
_notification_queue: asyncio.Queue = asyncio.Queue()
_notification_history: list[dict] = []


class NotificationManager:
    """Manages all notification channels."""
    
    def __init__(self):
        self.desktop_enabled = settings.enable_desktop_notifications
        self.sound_enabled = settings.enable_sound_alerts
        self._notifypy_available = False
        self._plyer_available = False
        self._twilio_client = None
        
        # Try to import notification libraries
        try:
            from notifypy import Notify  # notify-py package
            self._notifypy_available = True
        except ImportError:
            pass
        
        try:
            from plyer import notification
            self._plyer_available = True
        except ImportError:
            pass
        
        # Initialize Twilio if configured
        if settings.enable_sms and TWILIO_AVAILABLE and settings.twilio_account_sid:
            try:
                self._twilio_client = TwilioClient(
                    settings.twilio_account_sid,
                    settings.twilio_auth_token
                )
                logger.info("✅ SMS notifications enabled via Twilio")
            except Exception as e:
                logger.warning(f"Failed to initialize Twilio: {e}")
        
        if settings.enable_email and settings.smtp_username:
            logger.info("✅ Email notifications enabled")
        
        if settings.enable_pushover and settings.pushover_user_key:
            logger.info("✅ Pushover notifications enabled")
    
    def _should_send_phone_notification(self, severity: Severity) -> bool:
        """Check if this severity warrants a phone notification."""
        return severity.value in settings.phone_notify_severities
    
    async def send_notification(
        self,
        result: DetectionResult,
        wallet_address: str,
        trade_id: str,
    ):
        """Send notification through all enabled channels."""
        notification_data = {
            "id": f"{trade_id}_{datetime.utcnow().timestamp()}",
            "timestamp": datetime.utcnow().isoformat(),
            "title": result.title,
            "description": result.description,
            "severity": result.severity.value,
            "alert_type": result.alert_type.value if result.alert_type else None,
            "risk_score": result.risk_score,
            "wallet_address": wallet_address,
            "trade_id": trade_id,
            "metadata": result.metadata,
            "acknowledged": False,
        }
        
        # Add to in-app queue and history
        await _notification_queue.put(notification_data)
        _notification_history.append(notification_data)
        
        # Keep history bounded
        if len(_notification_history) > 1000:
            _notification_history.pop(0)
        
        # Send desktop notification
        if self.desktop_enabled:
            await self._send_desktop_notification(result)
        
        # Send phone notifications for high-severity alerts
        if self._should_send_phone_notification(result.severity):
            # SMS
            if settings.enable_sms:
                await self._send_sms(result)
            
            # Email
            if settings.enable_email:
                await self._send_email(result, wallet_address)
            
            # Pushover
            if settings.enable_pushover:
                await self._send_pushover(result)
        
        # Log the notification
        self._log_notification(result)
    
    async def _send_desktop_notification(self, result: DetectionResult):
        """Send a desktop notification."""
        title = result.title
        message = result.description[:200] + "..." if len(result.description) > 200 else result.description
        
        # Remove emojis for some notification systems
        clean_title = self._clean_for_notification(title)
        clean_message = self._clean_for_notification(message)
        
        # Always try OS-native notification first (most reliable)
        try:
            await self._send_os_notification(clean_title, clean_message, result.severity)
            return
        except Exception as e:
            logger.debug(f"OS notification failed, trying fallbacks: {e}")
        
        # Fallback to libraries
        try:
            if self._plyer_available:
                await self._send_plyer(clean_title, clean_message)
            elif self._notifypy_available:
                await self._send_notifypy(clean_title, clean_message, result.severity)
        except Exception as e:
            logger.warning(f"Desktop notification failed: {e}")
    
    async def _send_notifypy(self, title: str, message: str, severity: Severity):
        """Send notification using notifypy."""
        from notifypy import Notify
        
        notification = Notify()
        notification.title = title
        notification.message = message
        notification.application_name = "Polymarket Whale Detector"
        
        # Enable sound for high severity
        if severity in [Severity.HIGH, Severity.CRITICAL] and self.sound_enabled:
            notification.audio = None  # Use default system sound
        
        # Send in executor to not block
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, notification.send)
    
    async def _send_plyer(self, title: str, message: str):
        """Send notification using plyer."""
        from plyer import notification
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: notification.notify(
                title=title,
                message=message,
                app_name="Polymarket Whale Detector",
                timeout=10,
            )
        )
    
    async def _send_os_notification(self, title: str, message: str, severity: Severity):
        """Send notification using OS-specific commands."""
        system = platform.system()
        
        if system == "Darwin":  # macOS
            # Use osascript for macOS notifications
            # Escape quotes and special characters
            safe_title = title.replace('"', '\\"').replace('\n', ' ')
            safe_message = message.replace('"', '\\"').replace('\n', ' ')[:150]
            
            script = f'display notification "{safe_message}" with title "{safe_title}" sound name "default"'
            process = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()
            
        elif system == "Linux":
            # Use notify-send for Linux
            urgency = "critical" if severity == Severity.CRITICAL else "normal"
            process = await asyncio.create_subprocess_exec(
                "notify-send",
                "-u", urgency,
                title,
                message,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()
            
        elif system == "Windows":
            # PowerShell notification
            ps_script = f'''
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
            $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $text = $template.GetElementsByTagName("text")
            $text[0].AppendChild($template.CreateTextNode("{title}")) | Out-Null
            $text[1].AppendChild($template.CreateTextNode("{message}")) | Out-Null
            $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Polymarket Whale Detector").Show($toast)
            '''
            process = await asyncio.create_subprocess_exec(
                "powershell", "-Command", ps_script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()
    
    def _clean_for_notification(self, text: str) -> str:
        """Remove emojis and special characters for notification systems."""
        import re
        # Remove emojis
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F700-\U0001F77F"
            "\U0001F780-\U0001F7FF"
            "\U0001F800-\U0001F8FF"
            "\U0001F900-\U0001F9FF"
            "\U0001FA00-\U0001FA6F"
            "\U0001FA70-\U0001FAFF"
            "\U00002702-\U000027B0"
            "]+",
            flags=re.UNICODE
        )
        return emoji_pattern.sub("", text).strip()
    
    # ============ SMS (Twilio) ============
    
    async def _send_sms(self, result: DetectionResult):
        """Send SMS via Twilio."""
        if not self._twilio_client:
            return
        
        try:
            # Keep SMS short
            message = (
                f"🐋 POLYMARKET ALERT [{result.severity.value}]\n"
                f"{self._clean_for_notification(result.title)}\n"
                f"Risk: {result.risk_score:.0f}/100"
            )
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._twilio_client.messages.create(
                    body=message[:1600],  # SMS limit
                    from_=settings.twilio_from_number,
                    to=settings.twilio_to_number
                )
            )
            logger.info(f"📱 SMS sent: {result.title[:30]}...")
        except Exception as e:
            logger.error(f"SMS failed: {e}")
    
    # ============ Email ============
    
    async def _send_email(self, result: DetectionResult, wallet_address: str):
        """Send email notification."""
        if not AIOSMTPLIB_AVAILABLE or not settings.smtp_username:
            return
        
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"🐋 [{result.severity.value}] {self._clean_for_notification(result.title)}"
            msg["From"] = settings.smtp_username
            msg["To"] = settings.email_to or settings.smtp_username
            
            # Plain text version
            text_body = f"""
POLYMARKET WHALE ALERT
======================
Severity: {result.severity.value}
Risk Score: {result.risk_score:.0f}/100

{result.description}

Wallet: {wallet_address}
Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}

---
Polymarket Whale Detector
            """
            
            # HTML version
            severity_colors = {
                "LOW": "#28a745",
                "MEDIUM": "#ffc107", 
                "HIGH": "#dc3545",
                "CRITICAL": "#721c24"
            }
            color = severity_colors.get(result.severity.value, "#333")
            
            html_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background: {color}; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0;">🐋 Whale Alert</h1>
                    <p style="margin: 5px 0 0 0; font-size: 14px;">{result.severity.value} • Risk Score: {result.risk_score:.0f}/100</p>
                </div>
                <div style="background: #f8f9fa; padding: 20px; border: 1px solid #dee2e6; border-top: none; border-radius: 0 0 8px 8px;">
                    <h2 style="margin-top: 0;">{self._clean_for_notification(result.title)}</h2>
                    <pre style="background: white; padding: 15px; border-radius: 4px; overflow-x: auto; white-space: pre-wrap;">{result.description}</pre>
                    <p><strong>Wallet:</strong> <code>{wallet_address}</code></p>
                    <p><strong>Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                </div>
                <p style="color: #6c757d; font-size: 12px; text-align: center; margin-top: 20px;">
                    Polymarket Whale Detector
                </p>
            </body>
            </html>
            """
            
            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))
            
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                password=settings.smtp_password,
                start_tls=True,
            )
            logger.info(f"📧 Email sent: {result.title[:30]}...")
        except Exception as e:
            logger.error(f"Email failed: {e}")
    
    # ============ Pushover ============
    
    async def _send_pushover(self, result: DetectionResult):
        """Send push notification via Pushover."""
        if not settings.pushover_user_key or not settings.pushover_api_token:
            return
        
        try:
            import httpx
            
            # Map severity to Pushover priority
            priority_map = {
                Severity.LOW: -1,
                Severity.MEDIUM: 0,
                Severity.HIGH: 1,
                Severity.CRITICAL: 2,  # Emergency - requires acknowledgment
            }
            priority = priority_map.get(result.severity, 0)
            
            data = {
                "token": settings.pushover_api_token,
                "user": settings.pushover_user_key,
                "title": self._clean_for_notification(result.title),
                "message": result.description[:1024],
                "priority": priority,
                "sound": "cashregister" if result.severity in [Severity.HIGH, Severity.CRITICAL] else "pushover",
            }
            
            # Emergency priority requires retry/expire params
            if priority == 2:
                data["retry"] = 60  # Retry every 60 seconds
                data["expire"] = 3600  # Stop after 1 hour
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.pushover.net/1/messages.json",
                    data=data
                )
                response.raise_for_status()
            
            logger.info(f"📲 Pushover sent: {result.title[:30]}...")
        except Exception as e:
            logger.error(f"Pushover failed: {e}")
    
    def _log_notification(self, result: DetectionResult):
        """Log notification to console with formatting."""
        severity_colors = {
            Severity.LOW: "\033[32m",      # Green
            Severity.MEDIUM: "\033[33m",   # Yellow
            Severity.HIGH: "\033[91m",     # Light Red
            Severity.CRITICAL: "\033[31m", # Red
        }
        reset = "\033[0m"
        color = severity_colors.get(result.severity, "")
        
        logger.info(f"\n{'='*60}")
        logger.info(f"{color}[{result.severity.value}] {result.title}{reset}")
        logger.info(f"{result.description}")
        logger.info(f"Risk Score: {result.risk_score:.0f}/100")
        logger.info(f"{'='*60}\n")


# Singleton notifier
_notifier: Optional[NotificationManager] = None


def get_notifier() -> NotificationManager:
    """Get the notification manager singleton."""
    global _notifier
    if _notifier is None:
        _notifier = NotificationManager()
    return _notifier


async def get_pending_notifications(limit: int = 50) -> list[dict]:
    """Get pending notifications from the queue."""
    notifications = []
    while not _notification_queue.empty() and len(notifications) < limit:
        try:
            notification = _notification_queue.get_nowait()
            notifications.append(notification)
        except asyncio.QueueEmpty:
            break
    return notifications


async def get_notification_history(limit: int = 100) -> list[dict]:
    """Get notification history."""
    return _notification_history[-limit:]


async def clear_notification_history():
    """Clear notification history."""
    global _notification_history
    _notification_history = []


class ConsoleNotificationUI:
    """Rich console UI for displaying notifications."""
    
    def __init__(self):
        try:
            from rich.console import Console
            from rich.panel import Panel
            from rich.table import Table
            from rich.live import Live
            self.console = Console()
            self.rich_available = True
        except ImportError:
            self.rich_available = False
    
    def display_alert(self, result: DetectionResult, wallet: str):
        """Display an alert in the console with rich formatting."""
        if not self.rich_available:
            print(f"\n{'='*60}")
            print(f"[{result.severity.value}] {result.title}")
            print(result.description)
            print(f"Wallet: {wallet}")
            print(f"Risk Score: {result.risk_score:.0f}/100")
            print(f"{'='*60}\n")
            return
        
        from rich.panel import Panel
        from rich.text import Text
        
        severity_styles = {
            Severity.LOW: "green",
            Severity.MEDIUM: "yellow",
            Severity.HIGH: "red",
            Severity.CRITICAL: "bold red on white",
        }
        style = severity_styles.get(result.severity, "white")
        
        content = Text()
        content.append(result.description)
        content.append(f"\n\nWallet: {wallet}")
        content.append(f"\nRisk Score: {result.risk_score:.0f}/100")
        
        panel = Panel(
            content,
            title=result.title,
            border_style=style,
            subtitle=f"[{result.severity.value}]",
        )
        
        self.console.print(panel)
    
    def display_status(self, stats: dict):
        """Display monitoring status."""
        if not self.rich_available:
            print(f"Monitoring: {stats.get('markets_tracked', 0)} markets | "
                  f"Trades analyzed: {stats.get('trades_analyzed', 0)} | "
                  f"Alerts: {stats.get('alerts_generated', 0)}")
            return
        
        from rich.table import Table
        
        table = Table(title="📊 Monitoring Status", show_header=False)
        table.add_row("Markets Tracked", str(stats.get('markets_tracked', 0)))
        table.add_row("Trades Analyzed", str(stats.get('trades_analyzed', 0)))
        table.add_row("Alerts Generated", str(stats.get('alerts_generated', 0)))
        table.add_row("Wallets Flagged", str(stats.get('wallets_flagged', 0)))
        
        self.console.print(table)

