#!/usr/bin/env python3
"""Capture the ACTUAL visible Miro board through a manually logged-in Chromium."""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from miro_to_md import board_id

def main():
    load_dotenv()
    p=argparse.ArgumentParser()
    p.add_argument("--board",default=os.getenv("MIRO_BOARD_URL") or os.getenv("MIRO_BOARD_ID"))
    p.add_argument("--out",type=Path,default=Path("out/visual"))
    args=p.parse_args()
    if not args.board:
        p.error("Add MIRO_BOARD_URL to .env")
    from playwright.sync_api import sync_playwright
    args.out.mkdir(parents=True,exist_ok=True)
    profile=Path(".local/miro-chromium").resolve()
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=str(profile),
            headless=False,viewport={"width":1600,"height":960},device_scale_factor=1)
        page=ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://miro.com/app/board/"+board_id(args.board)+"/",
                  wait_until="domcontentloaded",timeout=60000)
        print("Войди в Miro вручную. Настрой масштаб/положение доски.")
        print("Enter в терминале = сохранить видимую область; q = закончить.")
        idx=0
        while True:
            action=input("[Enter] скриншот / q выход: ").strip().lower()
            if action=="q" or page.is_closed():
                break
            idx+=1
            filename=args.out/f"miro-view-{idx:02d}.png"
            page.screenshot(path=str(filename),full_page=False,
                            animations="disabled",timeout=60000)
            print("Сохранено:",filename)
        ctx.close()

if __name__=="__main__":
    main()
