import asyncio, sys
from harness import *
async def main():
    env = await Env().boot()
    u = env.actor(U, "user")
    for step in sys.argv[1:]:
        if step.startswith("t:"): await u.text(step[2:])
        elif step == "photo": await u.photo()
        else: await u.click(step)
        print(">>", step, "| state:", await u.state())
        for t in u.last_texts[-2:]: print("   TXT:", re.sub(r"[\u2800\u200b]","",t)[:500].replace("\n"," / "))
        print("   BTN:", [(b.get("text"), b.get("callback_data") or b.get("url")) for b in u.last_buttons])
    await env.close()
asyncio.run(main())
