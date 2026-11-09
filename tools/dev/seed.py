"""Demo conversations for the dev instance (fake Firestore only)."""
import datetime as dt

RICH_RESPONSE = """A **reverse SSH tunnel** lets a machine behind NAT or a firewall expose a local port on a public server. The connection starts from the private machine, so nothing needs to be opened on its router.

## How it works

1. The laptop opens an SSH connection to the server.
2. The server starts listening on a port, for example `11434`.
3. Anything that reaches that port travels back through the tunnel to the laptop.

```bash
ssh -N -R 127.0.0.1:11434:127.0.0.1:8080 xela@65.108.88.221 -p 2222
```

| Flag | Meaning |
|---|---|
| `-N` | no remote command, only forwarding |
| `-R` | remote port → local port |
| `-p 2222` | the server's SSH port |

> Keep the tunnel alive with a reconnect loop, otherwise a Wi-Fi drop ends it silently.

### Things to watch

- Bind the remote side to `127.0.0.1` so the port is not public.
- Use a dedicated key restricted with `permitlisten`.
- Check it with `ss -ltn | grep 11434` on the server.

Read more in the [OpenSSH manual](https://man.openbsd.org/ssh)."""

PY_RESPONSE = """Here is a small script that renames photos by the date they were taken:

```python
from pathlib import Path
from datetime import datetime

def rename_photos(folder: str) -> int:
    count = 0
    for photo in sorted(Path(folder).glob("*.jpg")):
        taken = datetime.fromtimestamp(photo.stat().st_mtime)
        photo.rename(photo.with_name(taken.strftime("%Y-%m-%d_%H%M%S") + photo.suffix))
        count += 1
    return count

print(rename_photos("~/Pictures"))
```

It uses the file's modification time. For the real capture time, read the EXIF data with `Pillow`."""

def seed(db, uid):
    now = dt.datetime.utcnow()
    sessions = [
        ("Reverse SSH tunnel explained", [("What is a reverse SSH tunnel? Give me an example command.", RICH_RESPONSE)], 0),
        ("Rename photos by date in Python", [("Write a Python script that renames my photos by date.", PY_RESPONSE)], 1),
        ("Weekly meal plan on a budget", [("Plan my meals for the week, cheap and healthy.", "Here's a simple plan built around **five base ingredients**: rice, lentils, eggs, frozen vegetables and chicken thighs.\n\n- **Monday:** lentil soup\n- **Tuesday:** fried rice with eggs\n- **Wednesday:** chicken and vegetables\n\nWant a shopping list too?")], 2),
        ("WSL disk attach access denied", [("WSL says access denied when attaching the disk.", "The `.vhdx` file belongs to the old account. Run `takeown` on the folder, then start WSL again.")], 26),
        ("Nevo marketing ideas for winter", [("Give me winter marketing ideas for Nevo.", "Three quick ideas: a winter recipes series, a delivery-time guarantee, and a referral bonus for December.")], 30),
        ("Restaurant revenue dashboard", [("Build a revenue dashboard for my restaurant.", "Sure. Which numbers matter most: daily covers, average ticket or food cost?")], 50),
        ("Cover letter for a backend role", [("Help me write a cover letter.", "Happy to. Paste the job description and a few lines about your experience.")], 80),
    ]
    for i, (name, turns, hours_ago) in enumerate(sessions):
        t = now - dt.timedelta(hours=hours_ago)
        sid = f"session_demo_{i}"
        messages = [{
            "id": f"m_{i}_{j}", "prompt": p, "response": r, "timestamp": t.isoformat(),
            "model": "qwen3.6-27b", "model_label": "Xela Qwen3.6 27B (Local GPU)",
        } for j, (p, r) in enumerate(turns)]
        db.collection("users").document(uid).collection("chat_sessions").document(sid).set({
            "session_name": name, "messages": messages, "pages": [], "active_page_id": None,
            "created_at": t, "last_updated": t, "last_message_at": t, "status": "active",
            "is_temporary": False, "is_pinned": False, "pinned_at": None, "project_id": None,
            "unread_count": 0, "last_read_at": t,
        })
    return len(sessions)
