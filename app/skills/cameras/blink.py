import json
import time
import asyncio
import traceback
import logging as logger

from pathlib import Path
from typing import Optional
from datetime import datetime

from blinkpy import api
from blinkpy.auth import Auth
from blinkpy.blinkpy import Blink

from app.skills.registry import register_tool
from app.core.credentials import CredentialsManager

_OLD_CREDENTIALS_FILE = Path(__file__).resolve().parent.parent.parent.parent / "blink_credentials.json"
SNAP_DELAY_SECONDS = 7

_blink_instance: Optional[Blink] = None
_migration_done: bool = False

def _migrate_legacy_blink_credentials() -> None:
    """Checks for old Blink credentials file and migrates to the new system if needed."""
    if not _OLD_CREDENTIALS_FILE.exists() or CredentialsManager.get("blink"):
        return
    
    try:
        old_creds = json.loads(_OLD_CREDENTIALS_FILE.read_text(encoding="utf-8"))
        CredentialsManager.save("blink", old_creds)
        logger.info("[BLINK] Successfully migrated legacy credentials.")
    except Exception as e:
        logger.warning(f"[BLINK] Failed to migrate legacy credentials: {e}")

async def _get_blink_instance() -> Blink:
    """Initializes and authenticates the Blink system."""
    global _blink_instance, _migration_done
    
    if _blink_instance:
        return _blink_instance

    if not _migration_done:
        _migrate_legacy_blink_credentials()
        _migration_done = True

    creds = CredentialsManager.get("blink") or {}

    if not creds.get("email"):
        raise RuntimeError(
            "Blink not configured. Open the Credentials tab and save your Blink email and password."
        )

    if not creds.get("token"):
        raise RuntimeError(
            "Blink not connected. Open the Credentials tab → Blink → click Login to complete authentication."
        )

    blink = Blink()
    blink.auth = Auth(creds, no_prompt=True)
    await blink.start()
    _blink_instance = blink
    return blink

async def _handle_arm(blink: Blink, arm: bool) -> str:
    """Helper to handle arming and disarming the system."""
    action_str = "armed" if arm else "disarmed"
    
    for name, sync in blink.sync.items():
        try:
            await sync.async_arm(arm)
        except Exception as e:
            logger.error(f"[BLINK] Failed to {action_str} sync module {name}: {e}")
            
    for name, cam in blink.cameras.items():
        try:
            await cam.async_arm(arm)
        except Exception as e:
            logger.warning(f"[BLINK] Failed to {action_str} camera {name}: {e}")
            
    return json.dumps({"status": "success", "message": f"Security grid {action_str}."})

async def _handle_snap(blink: Blink, camera_name: str) -> str:
    """Helper to handle taking a snapshot."""
    camera = blink.cameras.get(camera_name)
    if not camera:
        return json.dumps({"status": "error", "message": f"Camera '{camera_name}' not found."})
        
    try:
        await camera.snap_picture()
        await asyncio.sleep(SNAP_DELAY_SECONDS) 
        await camera.get_thumbnail()
        await blink.refresh(force=True)
        return json.dumps({"status": "success", "message": f"Snapshot captured for {camera_name}"})
    except Exception as e:
        logger.error(f"[BLINK] Failed to capture snapshot for {camera_name}: {e}")
        return json.dumps({"status": "error", "message": f"Failed to capture snapshot: {str(e)}"})

@register_tool("get_home_security_status")
async def execute_get_home_security_status(action: str = "status", camera_name: Optional[str] = None) -> str:
    try:
        blink = await _get_blink_instance()
        
        if action == "arm":
            return await _handle_arm(blink, True)
        elif action == "disarm":
            return await _handle_arm(blink, False)
        elif action == "snap" and camera_name:
            return await _handle_snap(blink, camera_name)
            
        await blink.refresh(force=False)
        grid_telemetry = []
        alerts = []
        
        system_armed = any(sync.arm for name, sync in blink.sync.items()) or any(getattr(cam, "arm", False) for name, cam in blink.cameras.items())
        
        if system_armed:
            alerts.append({"time": "SYSTEM", "message": "Security grid is actively armed.", "level": "alertred"})
        else:
            alerts.append({"time": "SYSTEM", "message": "Security grid is currently disarmed.", "level": "cautiongold"})
        
        all_events = []
        try:
            twenty_four_hours_ago = int(time.time()) - (24 * 3600)
            
            video_resp = await api.request_videos(blink, time=twenty_four_hours_ago, page=1)
            
            if video_resp and "media" in video_resp:
                for entry in video_resp.get("media", []):
                    cam_name = entry.get("device_name", "Unknown Camera")
                    created_at = entry.get("created_at")
                    
                    if created_at:
                        all_events.append({
                            'created_at': created_at,
                            'message': f"Motion detected by {cam_name}",
                            'type': 'motion'
                        })
        except Exception as e:
            logger.warning(f"[BLINK] Failed to fetch video history: {e}")
        
        all_events.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        for event in all_events[:8]:
            msg = str(event.get('message', event.get('type', 'Unknown System Event')))
            created = event.get('created_at', '')
            
            try:
                dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                time_str = dt.strftime("%H:%M")
            except (ValueError, TypeError):
                time_str = "LOG"
                
            lvl = "alertred" if any(k in msg.lower() for k in ['motion', 'person', 'occupied', 'detected']) else "sonargreen"
            alerts.append({"time": time_str, "message": msg, "level": lvl})

        for name, camera in blink.cameras.items():
            try:
                await camera.update()
            except Exception as e:
                logger.debug(f"[BLINK] Failed to update camera {name}: {e}")

            battery_status = str(camera.battery)
            status_color = "sonargreen"
            
            if battery_status.lower() not in ["ok", "good", "full"]:
                status_color = "alertred"
                alerts.append({"time": "WARNING", "message": f"{name} battery level degraded ({battery_status}).", "level": "alertred"})
                
            if camera.motion_detected:
                status_color = "alertred"

            grid_telemetry.append({
                "name": str(name),
                "temperature": camera.temperature,
                "battery": battery_status.upper(),
                "motion_detected": bool(camera.motion_detected),
                "color": status_color
            })

        return json.dumps({
            "status": "online",
            "system_armed": system_armed,
            "cameras": grid_telemetry,
            "alerts": alerts
        })

    except Exception as e:
        logger.error(f"[BLINK] Error in security status: {e}\n{traceback.format_exc()}")
        return json.dumps({"status": "error", "message": str(e)})
    
async def get_camera_image_bytes(camera_name: str) -> Optional[bytes]:
    """Helper function to fetch raw thumbnail bytes for the UI."""
    try:
        blink = await _get_blink_instance()
        camera = blink.cameras.get(camera_name)
        
        if camera and camera.image_from_cache:
            return camera.image_from_cache
        return None
    except Exception as e:
        logger.error(f"[W.A.D.E.] Failed to fetch image for {camera_name}: {e}")
        return None