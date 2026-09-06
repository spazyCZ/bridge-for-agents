"""Typed replies. The only place typing becomes a decision, so it fails safe."""
from __future__ import annotations

import pytest

from bridge_for_agents.replies import choice, permission, with_comment

OPTS = ["Postgres", "SQLite", "MySQL"]


# --- permissions: bare affirmatives only ---------------------------------
@pytest.mark.parametrize("text", ["y", "Y", "yes", "YES", "Yes.", "yep", "ok",
                                  "okay", "allow", "approve", "go", "sure", "👍", "✅"])
def test_a_bare_affirmative_allows(text):
    assert permission(text) == ("allow", "")


@pytest.mark.parametrize("text", ["n", "no", "No.", "nope", "nah", "deny",
                                  "stop", "cancel", "reject", "👎", "❌"])
def test_a_bare_negative_denies_with_no_reason(text):
    assert permission(text) == ("deny", "")


@pytest.mark.parametrize("text", [
    "yes but only the first one",
    "yes, if the tests pass",
    "ok once you have branched",
    "allow this but not the next",
    "sure — after the backup",
])
def test_a_hedged_affirmative_denies_rather_than_allowing(text):
    """The asymmetry that matters.

    Reading a hedge as approval runs the command; reading it as a denial only
    sends the prompt back to the terminal. One of those is recoverable.
    """
    behavior, reason = permission(text)
    assert behavior == "deny"
    assert reason == text          # Claude reads the whole caveat


@pytest.mark.parametrize("text,reason", [
    ("no, wrong branch", "wrong branch"),
    ("no - not on production", "not on production"),
    ("deny: the migration is not reviewed", "the migration is not reviewed"),
    ("stop, I'll do it by hand", "I'll do it by hand"),
])
def test_a_negative_with_a_reason_keeps_the_reason(text, reason):
    assert permission(text) == ("deny", reason)


def test_anything_else_denies_with_the_text_as_the_reason():
    assert permission("use the staging database instead") == (
        "deny", "use the staging database instead")


def test_a_word_containing_no_is_not_a_negative():
    behavior, reason = permission("nothing about this looks right")
    assert (behavior, reason) == ("deny", "nothing about this looks right")


# --- questions: number, label, and a comment -----------------------------
@pytest.mark.parametrize("text,expected", [
    ("1", "Postgres"), ("2", "SQLite"), ("3", "MySQL"), ("  2  ", "SQLite"),
])
def test_a_bare_number_picks_that_option(text, expected):
    assert choice(text, OPTS) == (expected, "")


@pytest.mark.parametrize("text", [
    "2 - but check the migration first",
    "2: but check the migration first",
    "2) but check the migration first",
    "2, but check the migration first",
    "2 — but check the migration first",
])
def test_a_number_and_a_comment(text):
    assert choice(text, OPTS) == ("SQLite", "but check the migration first")


def test_a_number_with_no_separator_stays_an_answer():
    """"3 replicas" is an answer, not a vote for option three."""
    assert choice("3 replicas", OPTS) == (None, "3 replicas")


def test_a_number_out_of_range_is_an_answer_not_a_wrong_option():
    assert choice("9", OPTS) == (None, "9")
    assert choice("0", OPTS) == (None, "0")


@pytest.mark.parametrize("text,label", [
    ("Postgres", "Postgres"), ("postgres", "Postgres"), ("SQLite", "SQLite"),
])
def test_an_option_can_be_named(text, label):
    assert choice(text, OPTS) == (label, "")


def test_a_named_option_can_carry_a_comment():
    assert choice("Postgres - it matches production", OPTS) == (
        "Postgres", "it matches production")


def test_free_text_that_names_no_option_is_the_answer():
    assert choice("neither, use DuckDB", OPTS) == (None, "neither, use DuckDB")


def test_empty_text_chooses_nothing():
    assert choice("   ", OPTS) == (None, "")


def test_no_options_means_everything_is_free_text():
    assert choice("1", []) == (None, "1")


# --- how it reaches Claude ------------------------------------------------
def test_a_comment_is_joined_to_the_choice():
    assert with_comment("SQLite", "check the migration") == "SQLite — check the migration"
    assert with_comment("SQLite", "") == "SQLite"


# --- the button value and a typed word must not share a namespace --------
def test_typing_ask_is_a_reason_not_a_terminal_fallback():
    """Found on a real run: "ask" matched the *button* value for
    "Answer in terminal", so typing the word silently fell back instead of
    denying. Only a button press means terminal."""
    assert permission("ask") == ("deny", "ask")


def test_typing_an_option_value_is_not_a_button_press():
    assert permission("allow") == ("allow", "")      # a bare affirmative, by word
    assert permission("allow it on staging only") == (
        "deny", "allow it on staging only")          # a sentence is not approval
