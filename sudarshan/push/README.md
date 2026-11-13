# sudarshan/push: Web Push (P6 F9)

The library the family service (`sudarshan-family`) uses to send Web Push notifications to family
members' phones: message encryption (RFC 8291 over RFC 8188's `aes128gcm`), VAPID (RFC 8292), the
endpoint check and a small async sender. It is built on `cryptography` and `httpx`, both already
pinned in `pyproject.toml`; nothing else is added, and `pywebpush` is not used. The owner's own
push, later, uses the same library inside `sudarshan-alerts` with a key pair of its own, held only
by the alerts (cross-design Y26).

## Using it (for F10)

```python
from sudarshan.push import DEFAULT_SUBJECT, HIGH, LOW, Outcome, PushSender, Subscription, Vapid

# At start: the pair the deploy's provision step made (root:sudarshan-family 640).
vapid = Vapid.from_files(
    "/etc/sudarshan-family/vapid_private.pem",
    "/etc/sudarshan-family/vapid_public.txt",  # optional; refused in words if it is another key's
    subject=setting or DEFAULT_SUBJECT,  # the contact, below
)
vapid.public_key  # 87 characters: the applicationServerKey the page subscribes with

# When a phone posts its subscription (PushSubscription.toJSON()): checked whole, or refused.
subscription = Subscription.from_json(posted)  # InvalidSubscription / EndpointRefused, in words
# A stored row: Subscription(endpoint, p256dh_text, auth_text) is checked again the same way.

# Sending: one request, never retried here.
sender = PushSender(client, vapid)  # the service's one httpx.AsyncClient
result = await sender.send(
    subscription, payload_json, ttl=21600, urgency=LOW, topic="digest-20261114"
)
```

- `payload` is bytes or text (sent as UTF-8), at most 3993 bytes: one record of 4096 (RFC 8291
  section 4). A longer one comes back as `TOO_LARGE` with no request made. Render the text at send
  time and keep only the result row afterwards (Y44): the library keeps no payload.
- `ttl` (required) is in seconds, 0 to 28 days. `urgency` is `very-low`, `low`, `normal` (the
  default) or `high`. `topic`, when given, is 1 to 32 letters, digits, `-` or `_` (RFC 8030
  section 5.4); a waiting message with the same topic is replaced, so `digest-<YYYYMMDD>` and
  `security-<kind>` fit. A wrong value is a `ValueError` before any request.
- `PushSender.stats` counts the outcomes, for `/health` (no names, endpoints or ids in it).
- Run sends in tasks stopped with `sudarshan.tasks.cancel_and_wait`: httpx can swallow a cancel
  while it connects (see `sudarshan/tasks.py`).

## Outcomes

| outcome | when | what the outbox does |
|---|---|---|
| `SENT` | any 2xx: 201, 202, and 200 (Microsoft's service) | done; the device's failures start again at 0 |
| `GONE` | 404, 410 | delete the subscription; the phone subscribes again when the app opens |
| `TOO_LARGE` | 413, or over 3993 bytes here (no request) | drop the message, keep the subscription |
| `SLOW_DOWN` | 429, and 406 from Microsoft (its documented throttle answer) | back off that endpoint; `retry_after` when the service gave one |
| `FAILING` | 5xx, a timeout, no connection (`status` is then None) | back off that endpoint and try later (`retry_after` from a 503 when given) |
| `REFUSED` | any other answer, a redirect included (never followed) | drop the message; it counts towards "Notifications may not arrive on this phone" |

`retry_after` is in seconds (from `Retry-After` as seconds or as a date), at most a day; F10 keeps
its own 10 minutes then 1 hour when there is none. `reason` is a sentence naming the push service
by its company ("Google's notification service did not answer in time; try again later."),
never an endpoint, a key or a status code; `status` keeps the code for the log.

## The endpoint check

An endpoint is data from a family member's phone, or from whoever holds a stolen family cookie, so
it is checked when a `Subscription` is built, before anything is stored or sent. It must:

- start with `https://`, carry no user name (`@`) and no fragment (`#`), and hold no space,
  control character, backslash or non-ASCII character, in at most 2048 characters;
- name port 443 or none, and a lowercase host name, never an IP literal (`127.0.0.1`, `[::1]`,
  `127.1`, `2130706433`);
- have a path, and name a host one of the services below serves;
- read the same way to `httpx.URL`, which is what sends it.

The four services, and why each is there:

| company | hosts | browsers | from |
|---|---|---|---|
| Google | `fcm.googleapis.com` | Chrome on Android and computers, and Chromium browsers on Google's service | Chrome's "Web Push Interoperability Wins": a subscription made with an `applicationServerKey` has the origin `fcm.googleapis.com` |
| Mozilla | `updates.push.services.mozilla.com` | Firefox on Android and computers | Mozilla's autopush documentation, "HTTP Endpoints for Notifications" |
| Apple | one label in front of `push.apple.com` (`web.push.apple.com`) | Safari 16 on macOS 13 and later; home-screen web apps on iOS 16.4 and later | Apple, "Sending web push notifications in web apps and browsers": allow `https://*.push.apple.com` |
| Microsoft | one label in front of `notify.windows.com` (`wns2-bl2p.notify.windows.com`) | Edge on Windows (Windows Push Notification Services) | Microsoft Q&A, a question about Edge web push that Microsoft answered, with endpoints `https://*.notify.windows.com/w/?token=*`; WNS's response codes page for 406 and `Retry-After` |

Left out on purpose: `android.googleapis.com` (the old GCM form, never given for a subscription
made with a VAPID key, and every SUDARSHAN subscription is); `jmt17.google.com` (in Pushpad's list
of hosts seen in about 200 million subscriptions, but in no browser's documentation: add it here
if a family phone's subscription is ever refused for naming it); Mozilla's staging and development
hosts (`updates-autopush.stage.mozaws.net`, `updates-autopush.dev.mozaws.net`). The family view is
for Android phones, so only the first two matter in v1; the other two cover the common desktop
browsers. A change to this list is a change to `PUSH_SERVICES` and to the test that pins it.

The sender builds each request itself and sends it as it is (`client.send(..., auth=None,
follow_redirects=False)`): none of the client's default headers, cookies, authentication or base
URL reach a push service, and a redirect is never followed. The client should still be made with
`trust_env=False` and no event hook that logs request addresses.

## VAPID

- The key pair is made once by root (`openssl ecparam -name prime256v1 -genkey -noout`, a SEC1
  PEM; `vapid_public.txt` is the uncompressed point as urlsafe base64 without padding, 87
  characters and a newline). `Vapid.from_files` also takes a PKCS 8 PEM, and refuses a key that is
  not P-256, a protected one, or a public file that is not this key's, each in words naming the
  file.
- Tokens are ES256 JWTs with `aud` the push service's origin, `exp` 12 hours ahead (RFC 8292: at
  most 24) and `sub` the contact. The signature is R and S side by side, 64 bytes, as JOSE wants,
  not DER. A token is reused for its origin until less than an hour of it is left (Apple asks for
  a new one no more than once an hour); at most 32 origins are kept.
- The contact (`sub`) defaults to `mailto:push@example.invalid`, a placeholder that names nobody
  (`.invalid` never resolves, RFC 2606). The owner may set his own `mailto:` or `https:` address
  through a setting of the family service (the design names `SUDARSHAN_FAMILY_PUSH_SUBJECT` in
  `/etc/sudarshan-family/family.env`; F2 or F10 reads it). No real address is ever written in the
  repository. Mozilla requires a `sub`, and Apple refuses one that is not a URL or `mailto:`.

## What it never does

- Log an endpoint, a key, a token, the contact or the payload (RFC 8030 section 8.5). It logs at
  debug only: the company, the outcome and the status. An exception's text is never logged (it can
  hold the address), only its kind. `test_push_hygiene.py` reads the names every log call in the
  package passes, the family modules' list included, and runs every outcome at debug looking for
  each secret. `repr()` of a `Subscription` names its push service only; of a `Vapid`, neither
  the contact nor a token.
- Retry, make an HTTP client, or close the one it is given.
- Wait without a limit: connect and pool 5 s, read and write 10 s, the whole send 20 s.

## Tests and vectors

`tests/vectors/` holds RFC 8291 section 5 and Appendix A, RFC 8188 section 3 and RFC 8292 section
2.4, typed by hand from the RFC texts (never produced by the code). The tests check every
intermediate value of RFC 8291's example, its body byte for byte, the JWT signing input of RFC
8292's example byte for byte, and a round trip through a receiver written in
`tests/push_testkit.py` from RFC 8188 and RFC 8291's user-agent side, which is first held to RFC
8188's own examples. Two lengths in the RFC texts are wrong and the vectors keep the corrected
ones: RFC 8291's body is 144 bytes (erratum 5230, reported by the RFC's author; the text says
145), and RFC 8188's first example is 53 (erratum 5516; the text says 54).

## Not modelled, and open

- Only one record and only `aes128gcm`, as RFC 8291 requires; no padding beyond the delimiter; no
  push message receipts (RFC 8030 section 5.1).
- Mozilla's documentation says a body may be limited to 2744 bytes on its bridged connections
  (how it reaches Firefox on Android), so about 2640 bytes of payload; the design's payloads stay
  under 3 KB, and under 2.6 KB they fit everywhere. A longer one to such a phone comes back
  `TOO_LARGE`.
- Whether Chrome fires `pushsubscriptionchange` reliably, what FCM answers in each case, and Doze
  with `Urgency: high` against `normal` are phone tests (P6 section 10, U16).
