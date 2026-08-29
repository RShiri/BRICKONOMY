"""Sign in to eBay once so sold prices become scrapeable.

  python -m brickonomy.ebay_login          # opens a real Chrome window
  python -m brickonomy.ebay_login --check  # just report whether it worked

eBay closed its sold/completed search to anonymous clients: every URL shape
answers with an error, sign-in or security page. A signed-in browser session
gets through, so this opens a visible Chrome using a dedicated profile
directory, waits while you log in normally, and then verifies by loading a
sold search and counting result cards.

Brickonomy never sees your password: you type it into eBay's own page, and the
session cookie lives in Chrome's profile folder (`.chrome-ebay/` next to the
app by default). Delete that folder to sign out. Set `ebay_profile_dir` to ""
in config.json to switch the whole thing off and keep eBay asks-only.
"""
import argparse
import time
from pathlib import Path

from .config import get_config
from .scrapers.ebay import SEARCH_URL, EbaySource

SIGNIN_URL = "https://www.ebay.com/signin/"
CHECK_URL = f"{SEARCH_URL}?_nkw=lego+75192&_ipg=120&LH_Sold=1&LH_Complete=1"


def _count_cards(html):
    from bs4 import BeautifulSoup
    return len(BeautifulSoup(html, "html.parser").select(".s-card, li.s-item"))


def check(profile_dir=None, log=print):
    """Load a sold search with the profile and report how many cards it sees."""
    from .scrapers.base import make_driver

    profile = profile_dir or EbaySource.signed_in_profile()
    if not profile:
        log("✘ no eBay profile yet — run: python -m brickonomy.ebay_login")
        return False

    driver = make_driver(profile_dir=profile)
    try:
        driver.get("https://www.ebay.com/")      # arrive with a referrer
        time.sleep(2)
        driver.get(CHECK_URL)
        html = driver.page_source
        title = driver.title
    finally:
        driver.quit()

    cards = _count_cards(html)
    if cards:
        log(f"✔ signed in — the sold search returned {cards} results.")
        log("  Sold prices will now be used for eBay on the next scan.")
        return True
    log(f"✘ still blocked (page title: {title!r}, 0 result cards).")
    log("  Run the login again and make sure you complete any 2-factor step.")
    return False


def login(log=print):
    from .scrapers.base import make_driver

    cfg = get_config()
    if not cfg.ebay_profile_dir:
        log("ebay_profile_dir is empty in the config — eBay stays asks-only.")
        return False
    profile = Path(cfg.ebay_profile_dir).expanduser()
    profile.mkdir(parents=True, exist_ok=True)

    log(f"Chrome profile: {profile}")
    log("A Chrome window will open on eBay's sign-in page.")
    log("Sign in normally (including any 2-factor step), then come back here.")

    # Not headless: this is the one moment a human has to drive the browser.
    driver = make_driver(profile_dir=str(profile), headless=False)
    try:
        driver.get(SIGNIN_URL)
        input("\n  Press Enter here once you are signed in… ")

        # Retry rather than give up. eBay's "Pardon Our Interruption"
        # interstitial is bot detection, not a login failure — it usually
        # clears once you interact with the page in the visible window, and
        # arriving from the homepage rather than cold helps.
        for attempt in range(1, 5):
            driver.get("https://www.ebay.com/")
            time.sleep(2)
            driver.get(CHECK_URL)
            time.sleep(2)
            cards, title = _count_cards(driver.page_source), driver.title
            if cards:
                log(f"\n✔ Done — the sold search returned {cards} results.")
                log("  eBay scans will now include sold prices, which raises")
                log("  its confidence from LOW to HIGH/MEDIUM in the blend.")
                return True

            log(f"\n  Attempt {attempt}: blocked — page title {title!r}.")
            if "interruption" in title.lower() or "security" in title.lower():
                log("  That is eBay's bot check, not a sign-in problem.")
                log("  In the Chrome window: solve whatever it shows (a")
                log("  button, a slider, sometimes just scrolling), until you")
                log("  can see real sold listings there.")
            else:
                log("  Make sure you are actually signed in in that window.")
            if input("  Press Enter to check again, or type q to stop: ").strip().lower() == "q":
                break
        title = driver.title
    finally:
        driver.quit()

    log("\n✘ Sold prices are not reachable from this machine right now.")
    log("  eBay stays asks-only; everything else keeps working. You can")
    log("  re-run this any time.")
    return False


def main():
    ap = argparse.ArgumentParser(description="Sign in to eBay for sold prices")
    ap.add_argument("--check", action="store_true",
                    help="only report whether the saved session still works")
    args = ap.parse_args()
    ok = check() if args.check else login()
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
