# sudarshan-family (P6, M2.6, M2.7): the family view's service

Family members see the account owner's books, view only. They never hold an order-capable
credential, and nothing in this service can place, change or cancel an order. It is the one ring
service that takes input from people other than the owner (sign-ins, phones, Telegram chats), so it
runs as its own account and can read none of the owner's secrets.

Status: on this branch (`feat/family-a`): the contract (F0), the store (F1), the accounts (F3), the
service and its unit (F2), the viewer API for sign-in and the member's own account (F4), and the
owner's notices, the store's nightly copy and the audit op (F11). On `feat/family-t2`: the family
bot (F8), Web Push, quiet hours, the day's summary and the outbox (F10; "Channels" below). The
data plane and the WebSocket dialect are other lanes; their routes answer "not ready" until they land.
`sudarshan/STATUS.md` has one row per feature. The service ships switched off
(`SUDARSHAN_FAMILY=off`).

```
Family phone (Chrome, PWA)
  data   /family/api/*  -> nginx -> sudarshan-family 127.0.0.1:5585   (session cookie, CSRF header)
  ticks  /family/ws     -> nginx -> sudarshan-family                  (cookie at the handshake)
OpenAlgo's owner bridge -> POST 127.0.0.1:5585/v1/admin/<op>          (sealed, principal owner)
sudarshan-alerts        -> POST 127.0.0.1:5585/v1/nudge               (sealed: "orders changed")
the deploy, the owner   -> GET  127.0.0.1:5585/health                 (counts and states only)
```

## The service

| | |
|---|---|
| unit | `sudarshan-family.service` (`ops/systemd/`), started only once `/etc/sudarshan-family/provisioned` exists |
| account | `sudarshan-family`: no login shell, no home, not in the group `sudarshan` |
| port | 5585 on 127.0.0.1, one listener for everything above (no second port, never another port) |
| switch | `SUDARSHAN_FAMILY` in `/etc/sudarshan-family/family.env`, off by default ("Switching the family view on", below) |
| contract | `contract.py`; its twin on the phone `frontend/src/sudarshan/family/types.ts` (`python -m sudarshan.family.gentypes`) |
| privacy table | `privacy_fields.json`: what each of the four categories hides (one table for every reader) |

## Its files

| path | what | who writes it |
|---|---|---|
| `/etc/sudarshan-family/` | root:sudarshan-family 750, files 640: read only by this service | the deploy's provision step |
| `seal-admin.key`, `seal-nudge.key`, `seal-internal.key`, `seal-portfolio-family.key`, `seal-journal-family.key` | the derived seal keys (urlsafe base64, 32 bytes each): the service never derives a key itself | provision (from `python -m sudarshan.family.derive`, run as `sudarshan`) |
| `store.key` | 32 random bytes (standard base64): the key of the secrets at rest in the store | provision, once |
| `vapid_private.pem`, `vapid_public.txt` | the Web Push key pair | provision, once |
| `telegram_bot_token` | the family bot's token (one line, as BotFather shows it): read again when the file changes | the owner, once (Channels, below); the deploy never writes it |
| `feed-token` | this service's token for the price feed's write calls | provision |
| `nse_holidays.txt`, `nse_extra_trading_days.txt` | copies of the exchange's day files | provision, when they change |
| `family.env` | the settings, read as the unit's environment | the deploy makes it from `ops/deploy/family-env.conf` (the switch, off); every value in it is the owner's from then on |
| `provisioned` | written only after `python -m sudarshan.family.check` passed as this account | provision |
| `/var/lib/sudarshan-family/family.db` | the store (folder 700, file 600) | this service only |
| `/var/lib/sudarshan-family-export/` | sudarshan-family:sudarshan 2750: what the ring may read | this service |
| `.../events.jsonl` | notices for the owner (0640), worded and sent by `sudarshan-alerts` | this service |
| `.../family-copy.db` | the store's copy for the nightly backup (0640), at 16:20 and 22:20 | this service |

The one-time set-up that makes the account, the folders and the firewall allow-list is
`ops/family/bootstrap.sh`; `sudarshan/README.md` says how the owner runs it.

## Switching the family view on (the owner's steps, in order)

The release installs with the view switched off: no family member can see anything until every
step below is done. Do them outside market hours the first time. Each step says what you should
see; stop at the first one that does not.

1. **The release is installed.** The Family page in your own menu opens and says the family view
   is not set up (or is switched off). Nothing else is needed for the install itself.
2. **The one-time family set-up**, if this server has not had it: "Running the set-up" in
   `sudarshan/README.md` (one pasted line, as root). You should see `PASS.` at the end. The set-up
   makes `/etc/sudarshan-family/family.env` (root: sudarshan-family, mode 640) holding
   `SUDARSHAN_FAMILY='off'`, from the list this release ships
   (`sudarshan/ops/deploy/family-env.conf`), and says `family: written in /etc/sudarshan-family: family.env` when it did.
   If it could not (GitHub not answering), the deploy tool makes the file at its next run: that
   step runs on every run, every ten minutes, also when there is nothing to install, and never
   changes a value you chose. The service then starts and answers "switched off".
3. **The nginx lines**: "Installing the nginx lines" below, steps 1 to 7. You should see
   `nginx: PASS. The family view's nginx lines are installed, loaded and this release's.`
4. **The family bot's token** (optional; the view works without it): "The family bot" below,
   "The owner's steps, once".
5. **The switch.** Paste this line in the SSH session. It sets `SUDARSHAN_FAMILY=on` in the family
   view's own settings file and in the alerts' (`/etc/sudarshan/alerts.env`, so a fill on your side
   reaches a family screen at once), keeps both files closed to other accounts, and restarts those
   two services and nothing else:

   ```sh
   sudo bash -c 'set -e; v=on; f=/etc/sudarshan-family/family.env; a=/etc/sudarshan/alerts.env; case "$v" in on|off) ;; *) echo "v must be on or off. Nothing was changed."; exit 1 ;; esac; test -f "$f" || { echo "There is no $f yet: run sudo sudarshan-deploy once (it makes the file after the set-up of step 2), then run this again. Nothing was changed."; exit 1; }; test -e "$a" || install -m 640 -o root -g sudarshan /dev/null "$a"; for x in "$f" "$a"; do sed -i "/^[[:space:]]*SUDARSHAN_FAMILY[[:space:]]*=/d" "$x"; test -z "$(tail -c 1 "$x")" || echo >> "$x"; echo "SUDARSHAN_FAMILY=$v" >> "$x"; grep -qx "SUDARSHAN_FAMILY=$v" "$x" || { echo "The switch could not be written in $x. Nothing was restarted."; exit 1; }; done; chown root:sudarshan-family "$f"; chmod 640 "$f"; systemctl restart sudarshan-family sudarshan-alerts; echo "The family view is switched $(sed -n "s/^SUDARSHAN_FAMILY=//p" "$f")."'
   ```

   You should see `The family view is switched on.` The last word is read back from the file, so
   it is what was written. A setting that was the last line of either file with no line end after
   it keeps its own line and its value. A later release never moves the switch back: a value you
   chose in either file stays.
6. **The Family page** now shows the service on and its check passed. Add a member and choose
   what their privacy hides. Then, on the Family page's Shares tab: under "Portfolio tabs" share
   the Upstox tab with them, and under the watchlists share any lists. Send the invite link
   yourself: it works once, for two hours.
7. **First-day checks.** A test member on your own phone joins and sees only what you shared; the
   server's address without `/family` shows the refusal page on that phone; a fill appears on the
   family positions within a few seconds; a list you stop sharing leaves the member's screen; and
   the deploy log has no line starting `WARNING: family`.

To switch it off again, run the line of step 5 with `v=off` in place of `v=on` (it appears once,
near the start of the line). You should see `The family view is switched off.` Every family
screen then reads that the view is switched off, and members, shares and privacy are kept as
they are.

### What decides what a member sees

| what | where you set it | enforced by |
|---|---|---|
| positions and holdings | the Upstox tab's share: the Family page, Shares tab, "Portfolio tabs" (view or restricted, with an optional end) | the portfolio service |
| orders and trades | that share, and the member's Orders switch | the portfolio service, and again here |
| funds | that share, and the member's Funds switch | the portfolio service, and again here |
| figures inside them (P&L, quantities) | the member's P&L and Quantities switches | the portfolio service, and again here |
| watchlists | the list's share, on the Family page | OpenAlgo's own note of shared lists |
| your other portfolio tabs | each tab's share, in the same "Portfolio tabs" section | the portfolio service |

- There is no separate share for a single book: a "book share" decided nothing, so the service
  refuses one ("Which books a member can open is set by sharing the Upstox tab under Portfolio
  tabs on the Family page, and by that member's Orders and Funds switches.") and the Family page
  no longer offers it.
- A share you remove, let expire or make restricted stops showing within a minute, at any hour:
  the family service asks the portfolio what each member is shared before it serves a figure it
  kept, once a minute at most, and drops what it kept when that changed.
- While the portfolio does not answer that question, a page waits three seconds for it, not the
  twelve a read may take, and says "What is shared with you could not be checked just now."
  over the figures it kept, with their time: they are never shown as current. After ten minutes
  without an answer the kept figures are put away and the screen says so; they come back when
  the portfolio answers again. A member sees less in that time, never more.
- How old a holding's price is (`fresh`: "Value from 1 Oct · 45 days old") is hidden by no
  privacy category: it carries no amount and no quantity, and a member who is shown no figures
  still has to know a number's age.
- P&L hidden puts the overall P&L out of reach, with every rupee figure and cost price. What
  it cannot hide beside a quantity: "With quantities shown, a member can still work out a day's
  change and a holding's value from the quantity and the price. Hide quantities to hide those
  too." (the privacy table's `pnl_hidden_note`, for the Family page to show under the switch).
- Signing in and joining are limited for everyone together (12 a minute) as well as for each
  connection (5 a minute), so that nobody can make the server do password work without end. The
  price: three connections trying 4 times a minute each can keep sign-in and join answering "Too
  many tries" for as long as they keep at it. A member already signed in is not affected (a
  session lasts 30 days), and nginx's error log names the addresses.

## For the pages

- Read `GET /family/api/me` once when a page opens: it answers the member (or 401 in words) and sets
  the CSRF cookie `__Host-sudarshan_family_csrf` when the browser has none. Every request that is not
  a GET carries that cookie's value in `X-Family-CSRF` and a JSON body; sign-in and join included.
- A 401 is `signed_out` with the sentence to show (expired, signed out on this device, removed,
  paused); a wrong sign-in name, password or code is one answer, `signin_failed`; `locked` (429)
  names the time to try again.
- The WebSocket closes with 4401 and "Signed out on this device." when the session ends: go to the
  sign-in page, do not reconnect.

## Modules

| path | what | built in |
|---|---|---|
| `contract.py`, `gentypes.py` | the contract and its twin on the phone | F0 |
| `store.py`, `migrations.py`, `migrate.py`, `audit.py`, `errors.py`, `words.py` | family.db, its steps and `--status`, the audit, the refusals and their sentences | F1 |
| `accounts.py`, `sessions.py`, `invites.py`, `lockout.py`, `members.py` | passwords, codes, sign-ins, sessions, invites, lockout, the owner's members | F3 |
| `service.py`, `listener.py`, `ws.py`, `admin_api.py`, `health.py`, `config.py`, `__main__.py` | the process, its one listener and its doors | F2 |
| `viewer_api.py` | the viewer API: cookies, CSRF, sign-in, the member's own account | F4 |
| `notices.py`, `backup.py` | the owner's notices, the store's copy for the nightly backup | F11 |
| `derive.py`, `check.py` | the one-time set-up's pieces (the deploy runs them) | O2' |
| `bot.py`, `bot_words.py`, `telegram_client.py`, `quiet.py` (market hours) | the family bot: link codes, the read-only command table, two languages, its own pacer per chat | F8 |
| `push.py`, `quiet.py`, `digest.py`, `outbox.py`, `timers.py` | Web Push, quiet hours and Settings, the day's summary, the outbox, and the `channels` job that runs them with the bot | F10 |
| `safe_http.py` | the channels' HTTP transport: a request a stop cancels while it connects leaves no socket open (httpx 0.28 with anyio 4.14 loses it otherwise) | F8, F10 |

## Channels

### The family bot (F8, M16.4)

A second Telegram bot, separate from the owner's: only this service holds its token, and it can
show what each member may see and nothing else. There is no command that places, changes or
cancels an order, and its HTTP client can reach only Telegram's API and two loopback services
(the portfolio on 5583, the price feed on 5580); a request anywhere else is refused before it
leaves.

| command | answers |
|---|---|
| `/status` | the market (open or closed, "Real-time" only while ticks arrive) and the indices |
| `/positions`, `/holdings` | the account owner's positions or holdings, under the member's privacy, with the time of the figures |
| `/orders` | today's orders, when orders are shared with the member |
| `/portfolio` | the portfolios shared with the member |
| `/quiet on`, `/quiet off` | quiet hours for the daily summary (sign-in notices are never quiet) |
| `/language en`, `/language gu` | English or Gujarati for the bot and the notifications |
| `/help`, `/stop` | the list; unlink this chat |

Anything else is answered "This bot only shows information. It cannot place, change or cancel an
order." A member links their chat from Settings, Link Telegram: the link works once, for 10
minutes. A chat that is not linked is only told how to link. Groups are never answered.

**The owner's steps, once.** The deploy does not make the token file; the owner puts it there.

1. In Telegram, open @BotFather and send `/newbot`. For the name, type
   `सुदर्शन / SUDARSHAN · Family`; for the user name, any free name ending in `bot`. BotFather
   answers with the token: a number, a colon and about 35 letters and digits. Copy it into your
   password manager; never paste it into a chat.
2. The family set-up (`sudarshan/ops/family/bootstrap.sh`) must have run, so that
   `/etc/sudarshan-family` exists on the server.
3. On the laptop, save the lines below as `sudarshan-family-bot.bat` (in Notepad: Save as, then
   Save as type "All files"), change `USER@SERVER` in its third line to the login you already use
   for the server, and double-click it:

   ```bat
   @echo off
   setlocal
   set "SERVER=USER@SERVER"
   ssh -t %SERVER% "sudo test -d /etc/sudarshan-family || { echo 'The family set-up has not been run on this server yet. Nothing was changed.'; exit 1; }; read -rs -p 'Family bot token (nothing shows as you paste it; press Enter after it): ' t; echo; [[ $t =~ ^[0-9]{5,16}:[A-Za-z0-9_-]{30,64}$ ]] || { echo 'Not saved: that is not a bot token as BotFather shows it. Nothing was changed.'; exit 1; }; printf '%%s\n' $t | sudo install -o root -g sudarshan-family -m 640 /dev/stdin /etc/sudarshan-family/telegram_bot_token && echo 'Saved. The family bot starts within a minute.'"
   if errorlevel 1 (
     echo The token was not saved: read the lines above, fix what they name and run this file again.
     pause
     exit /b 1
   )
   pause
   exit /b 0
   ```

   It asks for your password on the server once (`[sudo] password for ...:`), then for the token
   in a hidden prompt: paste it (a right-click pastes) and press Enter. Nothing shows as you
   paste, and the token is never on a screen, in a file on the laptop or in the shell history.
   The file it writes is `/etc/sudarshan-family/telegram_bot_token`, root:sudarshan-family 640.
4. Open Family on your phone: the Family bot card reads "Polling" within a minute. No restart is
   needed; the service reads the file again whenever it changes.

**When the card says otherwise.** "Token missing": the file is not there, or does not hold a token
as BotFather shows it (run step 3 again). "Telegram refused the token": the token was revoked or
mistyped; in @BotFather send `/token` (or `/revoke`, if it may have leaked), then run step 3 with
the new one. You also get one Telegram notice from the owner's bot when Telegram refuses a token.
If the service's journal says another program is using the token, a second copy of the bot is
running somewhere: stop it, or revoke the token and save a new one.

### Web Push, quiet hours and the day's summary (F10, M10.6, M10.9, M20.4)

A member turns notifications on per phone in Settings; the phone subscribes with this service's
own key (`vapid_public.txt`; the pair is made once by the provision step and held only here). Each
subscription is checked before it is kept: a known push service (Google, Mozilla, Apple, Microsoft),
https, no numeric address. A phone that signs out gets nothing more; three failed sends in a row
mark the phone ("Notifications may not arrive on this phone") and tell the owner once.

| message | when | push | Telegram | quiet hours |
|---|---|---|---|---|
| sign-in notice (a new phone, a locked sign-in) | at once | urgency high, topic `security-<kind>` | yes, when linked | never quiet |
| the day's summary | the last close + 25 min on trading days (16:05; 16:10 from 7 Dec 2026) | urgency low, TTL 6 h, topic `digest-<day>` | when chosen | held to the window's end inside its 6 h, else dropped with an audit line |
| a test (Settings, Send a test) | at once, this phone and the chat | urgency normal, TTL 60 s | when linked | never quiet |

Quiet hours are 21:00 to 08:00 unless the member changes them (Settings, or `/quiet on|off`). A
child is quiet outside the market session as well, so a child's 16:05 summary is dropped (with an
audit line) as the rules stand. The summary is six lines at most, through the member's privacy,
in their language: the day, the indices, positions (P&L when shown), the holdings' move today (when
P&L is shown), the journal's trades count (Y16: when orders are shown; won and lost with P&L too)
and the next trading day. Its text is made when it is sent, never stored; a summary the service
missed runs at its next start until 21:00, then the day is recorded as skipped.

`SUDARSHAN_FAMILY_JOURNAL_URL` (default `http://127.0.0.1:5586`) is where the summary asks the
journal for the day's counts; without the journal's family key (`seal-journal-family.key`) or an
answer, the summary goes without that line.

## Installing the nginx lines (O3)

Family phones reach the family view through nginx, and nginx keeps them out of everything else
of OpenAlgo. Three files in `sudarshan/ops/nginx/` do that, installed once by you and never
changed by the deploy tool:

| file | goes to | what it does |
|---|---|---|
| `sudarshan-family-zones.conf` | `/etc/nginx/conf.d/` | tells a family phone (the family view's cookie, without OpenAlgo's own) from your phone, lists what a family phone may reach, and sets the sign-in limits |
| `sudarshan-family.conf` | `/etc/nginx/snippets/` | the guard (one line at the top of the server, so it covers every location: pages, `/api`, `/auth`, `/sudarshan`, `/socket.io`, `/ws`), the refusal page, the family service's paths, the invite page |
| `sudarshan-family-proxy.conf` | `/etc/nginx/snippets/` | what the family service's paths share: the phone's own address, a 64 KiB cap, time limits, and nginx's own refusals in plain words |

What changes, once they are in:

- A family phone opening anything of OpenAlgo gets a page that says "This part of सुदर्शन /
  SUDARSHAN is for the account owner", with a button to the family view. It reaches only the
  family view (`/family/...`) and the files its pages load. OpenAlgo itself refuses such a phone
  anyway (it never holds an OpenAlgo session); nginx is the second lock.
  - The guard is one line at the top of the server rather than a line in each location, so every
    location the site has, or gains later, is covered.
  - The refusal page is held by nginx itself rather than taken from the app's build, so it leaves
    the site's own error pages alone and still answers while OpenAlgo restarts.
  - The server's address alone and About are refused too: both open your own app, whose every
    request the guard refuses, so a family phone gets the refusal page and its button instead of
    a broken page.
- Your phone and laptop, TradingView, Telegram and every other caller see no change: the guard
  acts only on a phone that carries the family cookie and not OpenAlgo's.
- Signing in and joining are held to 5 tries a minute from one connection, and 12 a minute from
  everyone together; past that the phone reads "Too many tries from this connection. Wait a
  minute, then try again." and the family service does no work for it. A refused sign-in is in
  nginx's error log with the address it came from.
- The family service's private routes and its `/health` cannot be reached from outside: nginx
  hands the service only `/family/api/...` and `/family/ws`, cleaned of `../` tricks.
- An invite link carries its code in the address, so the invite page and its details are kept
  out of nginx's access log, and the page asks the phone not to pass its address on to the
  requests it makes next.

### Your steps, once the release that carries the family view is installed

1. On the laptop, open the SSH session to the server.
2. Paste this line (it fetches the three files from GitHub at the release installed on the server,
   as the family set-up did, and copies them into place, owned by root, mode 644):

   ```sh
   sudo bash -c 'set -e; d="$(mktemp -d)"; trap "rm -rf \"$d\"" EXIT; . /etc/sudarshan/deploy.env; t="$(cat /var/lib/sudarshan/deployed_tag)"; for f in sudarshan-family-zones.conf sudarshan-family.conf sudarshan-family-proxy.conf; do curl -fsSL "https://raw.githubusercontent.com/${REPO#https://github.com/}/$t/sudarshan/ops/nginx/$f" -o "$d/$f"; done; install -m 644 -o root -g root "$d/sudarshan-family-zones.conf" /etc/nginx/conf.d/; install -m 644 -o root -g root "$d/sudarshan-family.conf" "$d/sudarshan-family-proxy.conf" /etc/nginx/snippets/; echo "copied the nginx files of $t"'
   ```

   You should see `copied the nginx files of v...`. A `curl` error means the installed release has
   no such files yet: nothing was copied.
3. Open the SUDARSHAN site (the file step 03-A wrote; `ls /etc/nginx/sites-enabled/` lists the
   sites): `sudo nano /etc/nginx/sites-enabled/<that file>`. In the `server { ... }` block that
   has `listen 443`, add this one line below its `ssl_`, `add_header`, `set` and `if` lines and
   above its first `location`, then save (Ctrl+O, Enter, Ctrl+X):

   ```nginx
       include snippets/sudarshan-family.conf;
   ```

4. Record which file that is, so the deploy tool's check reads that file and no other site's:

   ```sh
   echo /etc/nginx/sites-enabled/<that file> | sudo tee /etc/sudarshan/nginx-site
   ```

   You should see the path printed back.
5. Optional, and worth doing if OpenAlgo's `.env` sets `TRUST_PROXY_HEADERS = 'TRUE'` (the check
   warns when it does and these lines are missing): in the same file, inside the `location / { ... }`
   of that server block, add these two lines, because otherwise anyone can write an address of
   their choosing in these two headers, which OpenAlgo believes before nginx's own, and so put
   that address on OpenAlgo's ban list or step round its sign-in limiter:

   ```nginx
           proxy_set_header CF-Connecting-IP "";
           proxy_set_header True-Client-IP "";
   ```

6. Test, then reload. Never restart: a reload keeps every open connection, and if the test fails
   nothing is reloaded and the site keeps running as before.

   ```sh
   sudo nginx -t && sudo systemctl reload nginx
   ```

   You should see `syntax is ok` and `test is successful`. If not, the line nginx names is the
   one to fix (most often the include line outside the 443 server block); or undo (below).
7. Check it:

   ```sh
   sudo sudarshan-deploy --nginx-check
   ```

   You should see `nginx: PASS` lines and, last, `nginx: PASS. The family view's nginx lines are
   installed, loaded and this release's.` Each `nginx: FAIL` line says what to do, and a
   `nginx: WARN` line says what is worth doing (step 5). One FAIL you may meet: OpenAlgo's session
   cookie is named by `SESSION_COOKIE_NAME` in OpenAlgo's `.env` (`session` unless it was
   changed); if the check says the guard knows it under another name, put the name it gives in
   place of `__Secure-session` in `/etc/nginx/conf.d/sudarshan-family-zones.conf` (the line under
   `map $http_cookie $sud_owner`), then step 6 again.
8. On a family phone (a test member on your own phone works too): the family view opens, and the
   server's address without `/family` shows the refusal page.

The check reads the three files, the site file you recorded and OpenAlgo's `.env`, and nothing
else of nginx: no other site's file, and of `nginx -t` only whether it passes. From then on every
run of the deploy tool checks them again, reading only, and its log says it once if they change,
go missing or are not loaded, or when a later release brings new ones (then do steps 2 and 6
again; the include line stays). Nothing is said while none of the files is there.

`nginx -t` reads the whole of nginx's configuration, every site on the server with it, and the
deploy tool runs as root. So a ten-minute run asks for it only when one of these four files has
changed since it last asked, and keeps the answer until the next change; `sudo sudarshan-deploy
--nginx-check`, which you run yourself, asks every time. If a run's log says the test did not
pass "when these files last changed", run the check: it says what holds now.

**Trying the family view on your own phone.** While you are signed in to both, both work. Your own
sign-in ends at 03:30 and the family one does not, so the next morning your own pages show the
family refusal page: open the family view, Settings, tap Sign out next to this phone, then sign
in as usual. A second browser on the phone for the family view avoids this.

**To undo:** take the include line out of the site first, then
`sudo rm /etc/nginx/conf.d/sudarshan-family-zones.conf /etc/nginx/snippets/sudarshan-family.conf
/etc/nginx/snippets/sudarshan-family-proxy.conf /etc/sudarshan/nginx-site`, then step 6. Family
phones then cannot reach the family view (its pages say it is not set up); nothing else changes.
The two lines of step 5 can stay: they help OpenAlgo whether or not the family view is there.

**What this does not cover.**

- A family phone that once opened your own sign-in page holds an OpenAlgo cookie (an empty one,
  without your session), and nginx then takes it for yours: that phone meets OpenAlgo as any
  stranger does, and OpenAlgo refuses it, but it sees OpenAlgo's sign-in page instead of the
  refusal page. Clearing the site's data in Chrome on that phone and signing in to the family view
  again brings the refusal page back.
- nginx's error log can still name an invite's address if the family service is down at that
  moment (OpenAlgo's own logs mask the code). An invite works once, for two hours.
- The family account's firewall (made by the one-time set-up, and proved by the family check at
  every deploy run) refuses your ports, OpenAlgo's market-data bus (5555) and its WebSocket proxy
  (8765) among them, and every local port the family view does not use. It allows OpenAlgo itself
  on 127.0.0.1:5000, which the family view reads the shared watchlists through. So a break-in into
  the family service could reach OpenAlgo there directly, without nginx and its guard, as a
  stranger: the sign-in page and the public pages, never an order, which needs your session or API
  key. If OpenAlgo's `.env` sets `TRUST_PROXY_HEADERS = 'TRUE'`, such a process could also give any
  address as its own, and so draw OpenAlgo's ban of an address onto yours or step round its sign-in
  limit; with `'FALSE'`, OpenAlgo's own default, it cannot. Step 5 does not help there: such a
  process does not pass through nginx.

Tests: `sudarshan/family/tests/test_nginx_text.py` (every rule on the files' text, and the same
files run in a real nginx in front of fake services when an nginx binary is found:
`SUDARSHAN_NGINX`, then `nginx` on the PATH, then `/usr/sbin/nginx`) and
`sudarshan/ops/tests/test_deploy_nginx_check.py` (the deploy tool's check).
