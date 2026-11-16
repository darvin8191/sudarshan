"""What the chart documents' bridge says to the owner (M4.18). Sentences for a trader: the cause
and what to do, never a code. Each device keeps its own copy whatever the server answers, so every
refusal says so."""

MESSAGES = {
    "invalid": "SUDARSHAN could not read that chart change, so the server copy was not changed. "
    "This device keeps its own copy. Update the app and try again.",
    "stale": "This was changed on another device first. That copy is sent back so this device can "
    "show it.",
    "too_big": "That is larger than the server keeps for one chart document (5 MB), so it is kept "
    "on this device only.",
    "limit": "The server already keeps as many of these as it holds, so this one is kept on this "
    "device only.",
    "off": "Saving charts on the server is switched off. Each device keeps its own drawings, "
    "indicators and layouts.",
    "record": "This server's chart record cannot be used right now. Each device keeps its own "
    "copy. Ask for help if this stays.",
    "busy": "That was too many chart saves in a minute. This device keeps its copy and saves it "
    "again with your next change.",
    "failed": "SUDARSHAN could not save that on the server just now. This device keeps its copy; "
    "try again in a minute.",
    "session": "You are signed out. Sign in again to keep charts on the server; this device keeps "
    "its own copy meanwhile.",
    "owner_only": "Saved charts are the owner's. This sign-in cannot read or change them.",
}

RECORD_NEWER = (
    "This server's chart record was written by a newer release of SUDARSHAN, so it is left "
    "untouched. Each device keeps its own copy until the server runs that release again."
)
RECORD_UNREADABLE = (
    "This server's chart record could not be opened. Each device keeps its own copy. Ask for help "
    "if this stays."
)
