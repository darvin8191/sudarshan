"""The family service's sentences (design 6.7) and its refusals: one sentence per key of the contract,
written for a person (no status code, no exception, no protocol word, no icon), and every refusal an
error code of the contract with its status and envelope."""

from __future__ import annotations

import re
import unicodedata

import pytest

from sudarshan.family import contract, words
from sudarshan.family.errors import (
    FamilyError,
    Invalid,
    LimitReached,
    NotFound,
    SchemaTooNew,
    StoreError,
    VersionConflict,
)

#: Words a family member or the owner must never read (CLAUDE.md, the rules file).
BANNED = re.compile(
    r"\b(HTTP|[45]\d\d|exception|error code|traceback|sqlite|database is locked|token|"
    r"paper|virtual|arm|armed|arming|null|None)\b",
    re.IGNORECASE,
)


def test_one_sentence_for_every_key_of_the_contract():
    assert tuple(words.WORDS) == contract.WORD_KEYS


@pytest.mark.parametrize("key", contract.WORD_KEYS)
def test_each_sentence_is_for_a_person(key):
    text = words.WORDS[key]
    assert text and text == text.strip() and text[-1] in ".?", key
    assert not BANNED.search(text.replace("{time}", "")), (key, text)
    for ch in text:
        assert ch.isascii() or unicodedata.category(ch)[0] in "LNPZ", (key, ch)  # no icon


def test_the_design_sentences_are_kept_word_for_word():
    assert words.say("expired") == "Your sign-in has expired. Sign in again."
    assert words.say("signed_out") == "You were signed out on this device."
    assert words.say("removed") == "This member was removed by the account owner."
    assert words.say("locked", time="19:05") == "Too many wrong attempts. Try again at 19:05."
    assert words.say("off") == "The family view is switched off on the server."
    assert words.say("ws_signed_out") == "Signed out on this device."
    assert words.say("not_shared") == "Not shared with you."


def test_a_wrong_name_password_or_code_get_one_sentence():
    """6.7 names one sentence for a wrong name and a wrong password; the code joins them, so a right
    password is never confirmed by the answer to a wrong code."""
    assert words.say("signin_failed") == "That sign-in name, password or code did not match."


def test_the_numbers_in_the_sentences_are_the_contracts():
    lim = contract.LIMITS
    assert f"{lim['handle_min']} to {lim['handle_max']}" in words.say("handle_rule")
    assert f"{lim['password_min']} to {lim['password_max']}" in words.say("password_rule")
    assert f"room for {lim['max_members']} members" in words.say("member_limit")


def test_every_refusal_is_a_code_with_its_status_and_envelope():
    for made, code in [
        (Invalid("handle_rule", field="handle"), "invalid"),
        (NotFound(), "not_found"),
        (VersionConflict(), "conflict"),
        (LimitReached("member_limit"), "limit"),
        (StoreError("disk full"), "failed"),
        (SchemaTooNew(3, 1), "store_newer"),
        (FamilyError("locked", time="09:30"), "locked"),
    ]:
        assert made.code == code and made.status == contract.ERROR_STATUS[code]
        body = made.body()
        assert body["status"] == "error" and body["code"] == code
        assert body["message"] == str(made) and "cause" not in body
    assert Invalid("handle_rule", field="handle").body()["field"] == "handle"
    assert "field" not in NotFound().body()
    assert StoreError("disk full").cause == "disk full"
    assert "disk full" not in str(StoreError("disk full"))
    with pytest.raises(ValueError):
        FamilyError("teapot")
