"""F9: the endpoint check. A subscription's endpoint is data from a family member's phone (or from
whoever holds a stolen family cookie), so it must name one of the browsers' push services, over
https on port 443, before anything is sent; otherwise the family service could be made to post to
anywhere, its own loopback services included. Every refusal is a sentence that never repeats the
address."""

from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from push_testkit import b64url, point

from sudarshan.push import (
    PUSH_SERVICES,
    EndpointRefused,
    InvalidSubscription,
    PushError,
    Subscription,
    check_endpoint,
)

KEY = point(ec.generate_private_key(ec.SECP256R1()))
SECRET = os.urandom(16)

ACCEPTED = [
    # endpoint, push service, origin (the VAPID audience)
    (
        "https://fcm.googleapis.com/fcm/send/cJ4kZ:APA91bE-x_y",
        "Google",
        "https://fcm.googleapis.com",
    ),
    ("https://fcm.googleapis.com/wp/cJ4kZAPA91bE", "Google", "https://fcm.googleapis.com"),
    ("https://fcm.googleapis.com:443/fcm/send/cJ4kZ", "Google", "https://fcm.googleapis.com"),
    (
        "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABnExample",
        "Mozilla",
        "https://updates.push.services.mozilla.com",
    ),
    ("https://web.push.apple.com/QGexampleToken", "Apple", "https://web.push.apple.com"),
    (
        "https://wns2-bl2p.notify.windows.com/w/?token=BQYAAAexample%2b%3d",
        "Microsoft",
        "https://wns2-bl2p.notify.windows.com",
    ),
]

REFUSED = {
    # what a phone could send instead, and the words it is refused with
    "http://fcm.googleapis.com/fcm/send/x": "is not a secure (https) address",
    "HTTPS://fcm.googleapis.com/fcm/send/x": "is not a secure (https) address",
    "https:fcm.googleapis.com/fcm/send/x": "is not a secure (https) address",
    "ftp://fcm.googleapis.com/fcm/send/x": "is not a secure (https) address",
    "https:///fcm.googleapis.com/x": "does not name a host the way notification services do",
    "https://fcm.googleapis.com:8443/fcm/send/x": "names a port other than the standard secure one",
    "https://fcm.googleapis.com:/fcm/send/x": "names a port other than the standard secure one",
    "https://fcm.googleapis.com:0443/fcm/send/x": "names a port other than the standard secure one",
    "https://fcm.googleapis.com:443:443/x": "names a port other than the standard secure one",
    "https://user@fcm.googleapis.com/fcm/send/x": "carries a user name",
    "https://user:pw@fcm.googleapis.com/fcm/send/x": "carries a user name",
    "https://fcm.googleapis.com@evil.example/x": "carries a user name",
    "https://fcm.googleapis.com\\@evil.example/x": "holds spaces or characters",
    "https://evil.example#@fcm.googleapis.com/x": "carries a part after '#'",
    "https://fcm.googleapis.com/fcm/send/x#part": "carries a part after '#'",
    "https://evil.example?@fcm.googleapis.com/x": "has no path a notification service makes",
    "https://127.0.0.1/fcm/send/x": "is a numeric address",
    "https://127.0.0.1:5000/api/v1/placeorder": "is a numeric address",
    "https://127.0.0.1:5583/v1/api": "is a numeric address",
    "https://169.254.169.254/latest/meta-data/": "is a numeric address",
    "https://[::1]/fcm/send/x": "is a numeric address",
    "https://[::ffff:127.0.0.1]:443/x": "is a numeric address",
    "https://127.1/fcm/send/x": "is a numeric address",
    "https://2130706433/fcm/send/x": "is a numeric address",
    "https://localhost/fcm/send/x": "does not name a host the way notification services do",
    "https://FCM.GOOGLEAPIS.COM/fcm/send/x": "does not name a host the way notification services do",
    "https://fcm.googleapis.com./fcm/send/x": "does not name a host the way notification services do",
    "https://fcm%2egoogleapis.com/x": "does not name a host the way notification services do",
    "https://-x.notify.windows.com/w/?token=x": "does not name a host the way notification services do",
    "https://fcm.googleapis.com": "has no path a notification service makes",
    "https://fcm.googleapis.com/": "has no path a notification service makes",
    "https://fcm.googleapis.com?x=1": "has no path a notification service makes",
    "https://fcm.googleapis.com/fcm/send/<x>": "has no path a notification service makes",
    'https://fcm.googleapis.com/fcm/send/"x"': "has no path a notification service makes",
    "https://fcm.googleapis.com/fcm send/x": "holds spaces or characters",
    "https://fcm.googleapis.com/fcm/send/x\n": "holds spaces or characters",
    "https://fcm.googleapis.com/fcm/\tsend/x": "holds spaces or characters",
    "https://fcm.googleapis.com/fcm/send/é": "holds spaces or characters",
    "https://fcm.googleapis.com.evil.example/fcm/send/x": "is not at one of the known notification services",
    "https://evil.example/fcm.googleapis.com/x": "is not at one of the known notification services",
    "https://evilfcm.googleapis.com/fcm/send/x": "is not at one of the known notification services",
    "https://notify.windows.com/w/?token=x": "is not at one of the known notification services",
    "https://a.b.notify.windows.com/w/?token=x": "is not at one of the known notification services",
    "https://push.apple.com.evil.example/x": "is not at one of the known notification services",
    "https://android.googleapis.com/gcm/send/x": "is not at one of the known notification services",
    "https://jmt17.google.com/fcm/send/x": "is not at one of the known notification services",
    "https://updates-autopush.stage.mozaws.net/wpush/v1/x": "is not at one of the known notification services",
    "https://push.example.net/push/JzLQ3raZJfFBR0aqvOMsLrt54w4rJUsV": "is not at one of the known notification services",
}


@pytest.mark.parametrize("endpoint, company, origin", ACCEPTED)
def test_the_browsers_push_services_are_accepted(endpoint, company, origin):
    checked = check_endpoint(endpoint)
    assert (checked.service.name, checked.origin) == (company, origin)
    assert checked.host == origin.removeprefix("https://")


@pytest.mark.parametrize("endpoint, words", sorted(REFUSED.items()))
def test_anything_else_is_refused_in_words_that_never_repeat_it(endpoint, words):
    with pytest.raises(EndpointRefused) as refused:
        check_endpoint(endpoint)
    said = str(refused.value)
    assert said.startswith("The notification address ") and said.endswith(
        ", so nothing is sent to it."
    )
    assert words in said
    assert endpoint not in said and "evil" not in said and "/fcm/send" not in said
    assert isinstance(refused.value, InvalidSubscription)  # so a PushError, so a ValueError
    assert issubclass(InvalidSubscription, PushError) and issubclass(PushError, ValueError)


@pytest.mark.parametrize("endpoint", ["", None, 12345, b"https://fcm.googleapis.com/fcm/send/x"])
def test_no_address_at_all(endpoint):
    with pytest.raises(EndpointRefused, match="^The phone sent no notification address.$"):
        check_endpoint(endpoint)


def test_an_address_longer_than_any_push_service_makes():
    check_endpoint("https://fcm.googleapis.com/fcm/send/" + "a" * 2000)
    with pytest.raises(EndpointRefused, match="is longer than any notification service makes"):
        check_endpoint("https://fcm.googleapis.com/fcm/send/" + "a" * 2020)


def test_the_list_of_push_services_is_exactly_the_four_documented():
    assert [(s.name, s.host, s.wildcard, sorted(s.slow_down)) for s in PUSH_SERVICES] == [
        ("Google", "fcm.googleapis.com", False, [429]),
        ("Mozilla", "updates.push.services.mozilla.com", False, [429]),
        ("Apple", "push.apple.com", True, [429]),
        ("Microsoft", "notify.windows.com", True, [406, 429]),
    ]


# ---- A Subscription is checked whole when it is built


def browser_json(endpoint="https://fcm.googleapis.com/fcm/send/cJ4kZ:APA91bE"):
    """What PushSubscription.toJSON() gives in Chrome."""
    return {
        "endpoint": endpoint,
        "expirationTime": None,
        "keys": {"p256dh": b64url(KEY), "auth": b64url(SECRET)},
    }


def test_a_subscription_from_the_browsers_json():
    subscription = Subscription.from_json(browser_json())
    assert (subscription.p256dh, subscription.auth) == (KEY, SECRET)
    assert subscription.target.origin == "https://fcm.googleapis.com"
    assert subscription == Subscription(subscription.endpoint, KEY, SECRET)  # text or bytes alike
    assert len({subscription, Subscription(subscription.endpoint, KEY, SECRET)}) == 1


def test_a_subscription_is_refused_before_it_can_be_stored():
    with pytest.raises(EndpointRefused, match="is a numeric address"):
        Subscription.from_json(browser_json("https://127.0.0.1:5000/api/v1/placeorder"))
    with pytest.raises(InvalidSubscription, match="notification key is not usable"):
        Subscription("https://fcm.googleapis.com/fcm/send/x", b"\x04" + bytes(64), SECRET)
    with pytest.raises(InvalidSubscription, match="notification secret is not usable"):
        Subscription("https://fcm.googleapis.com/fcm/send/x", KEY, b"short")
    for shape in (None, [], {"endpoint": "x"}, {"endpoint": "x", "keys": "p256dh"}):
        with pytest.raises(InvalidSubscription, match="did not send a notification subscription"):
            Subscription.from_json(shape)


def test_a_subscription_in_a_log_or_a_traceback_gives_nothing_away():
    subscription = Subscription.from_json(browser_json())
    for text in (repr(subscription), str(subscription), f"{subscription!r}"):
        assert text == "Subscription(service='Google')"
