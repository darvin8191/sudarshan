# सुदर्शन / SUDARSHAN changelog

Releases of the `sudarshan` branch, newest first, written for the owner deciding whether to
publish. The owner publishes each release on GitHub. The server's deploy timer then installs it
outside 09:00-15:45 IST on trading days, keeps backups, and rolls back if the app does not come up
healthy.

## v0.2.0 (20 Oct 2026)

### What changes for you
- **New look (design "D"), light theme only.**
  - Colours: peacock blue (#0E5A73) and gold (#E3A21A).
  - App icon: still the "सु" letter, now on peacock with a gold ring.
  - Name: सुदर्शन is drawn with the same letters as the icon.
  - Fonts: Barlow for text, Khand for headings and large figures.
  - Gains are green with an up arrow and a plus sign. Losses are red with a down arrow and a minus sign.
- **Dashboard, live.** It shows:
  - today's P&L (realised plus unrealised). The unrealised part moves with every price tick of
    your open positions. It is checked against Upstox's own figures whenever an order is placed,
    fills or is rejected, and every 45 seconds while the market is open;
  - funds available, and how much margin is in use, updated with those checks;
  - NIFTY 50, NIFTY BANK and INDIA VIX, moving tick by tick;
  - a holdings summary.

  "Live" beside the figures means prices are arriving. If they stop for 10 seconds while the
  market is open, it says "Stale" and how long ago the last update came, and the figures that
  should be moving turn grey (an index also gets a clock mark). After the close it says "Market
  closed". When the phone wakes up, or the price feed drops, the app reconnects by itself and
  says "Reconnecting" with the age of the figures until prices arrive again; it never says
  "Live" in that time. If reconnecting takes a while, prices are fetched every few seconds until
  the feed is back. All figures come from your account; nothing is sample data.
- **Login page.** New layout. If Chrome has saved your password, it can fill it in after your
  fingerprint: Chrome, then Settings, then Google Password Manager, then "Verify it's you to
  autofill passwords".
- **Auto login (optional, off until you switch it on).** Find it in the profile menu under Auto login.
  - Save your Upstox TOTP key and PIN once. They are stored encrypted on the server and never shown again.
  - On trading days the server then gets the Upstox token for you at about 08:35, and Telegram
    tells you whether it worked.
  - **Test now** shows the result immediately. It is refused from 09:00 to 15:30 while you are
    already connected, because it replaces the day's login.
- **Home-screen icon.** Your existing shortcut keeps its old icon. For the new one, remove the
  shortcut and add it again from Chrome (menu, then Add to Home screen). It still opens in Chrome.

### What is refused now
- After any rejected sign-in (wrong PIN or TOTP key), auto login pauses until you save the key or
  PIN again, so a wrong PIN is never retried by itself. Test now is refused while it is paused.
- Between 09:15 and 15:30, auto login signs in only when there is no working login (the stored
  one has expired, or you logged out). It never replaces a working session. When Upstox cannot
  confirm either way, it does not sign in and tells you.
- Updates are not installed at any time on a special trading day (a weekend or holiday session
  such as Muhurat trading) once that date is in the server's extra trading days list.

### Not in this release yet
- Watchlists, the screener and alerts. Their screens are designed and come next.
- The holdings summary on the dashboard is read when the page opens, not tick by tick yet.
- Positions, Tradebook and the other inner pages still use the older green and red shades.
- The login page does not show whether NSE is open, because that needs data that only exists
  after login.
- Passkey fingerprint login needs a domain name. On the IP address, Chrome's password manager
  with fingerprint is used instead.
- The auto-login TOTP endpoint and its error codes have not yet been seen live. The first
  **Test now** is the live check.

## v0.1.0 (20 Oct 2026)
- `sudarshan-feed`: one Upstox market-data connection (Analytics Token) that all SUDARSHAN
  services share. It opens at 08:58 and closes at 15:40 IST on trading days. There is no visible
  change in the app yet.

## v0.0.1 (20 Oct 2026)
- First release through the new pipeline. GitHub builds the app; the server installs it outside
  market hours with database backups and an automatic rollback.
