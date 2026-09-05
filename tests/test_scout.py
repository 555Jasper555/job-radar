"""Known-answer tests for gigs/scout.py classify(): buyers in, help-seekers out."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gigs"))
import scout  # noqa: E402


def row(title, body="", sub="r/forhire", mode="marker", source="reddit", hours=5, comments=1, author="buyer"):
    return {"id": "t1", "source": source, "sub": sub, "title": title, "url": "https://example.test/p",
            "author": author, "created": datetime.now(timezone.utc) - timedelta(hours=hours),
            "comments": comments, "body": body, "_mode": mode, "flair": ""}


def test_hiring_post_with_dollar_figure_is_explicit():
    r = scout.classify(row("[Hiring] Need a Next.js dashboard built on Supabase — $400 budget",
                           "Looking for someone to build an internal dashboard. Budget $400, DM me."))
    assert r and r["tier"] == "explicit"
    assert r["amount_usd"] == 400
    assert "app-build" in r["fit"]
    assert r["apply"] == "dm"


def test_task_post_on_slavelabour_scrape_is_kept():
    r = scout.classify(row("[TASK] Scrape 500 Google Maps listings into a CSV, $30",
                           "Need business name, phone, rating. Will pay $30 via PayPal.", sub="r/slavelabour"))
    assert r and r["tier"] == "explicit" and "scrape-data" in r["fit"]


def test_help_seeker_question_is_rejected():
    assert scout.classify(row("How do I get my PWA into the App Store? Stripe keeps failing review",
                              "I've been stuck for weeks, any advice appreciated")) is None


def test_self_advert_is_rejected():
    assert scout.classify(row("[For Hire] Full-stack developer, React/Node, $25/hr",
                              "I am available for freelance work, 5 years experience")) is None


def test_no_pay_language_is_rejected():
    assert scout.classify(row("[Hiring] Someone to build an AI chatbot for my site",
                              "Looking for a developer, message me with your portfolio")) is None


def test_pay_words_without_figure_is_stated_tier():
    r = scout.classify(row("[Hiring] Python scraper for a real-estate site",
                           "Paid project, hourly rate negotiable, send samples"))
    assert r and r["tier"] == "stated"


def test_design_work_is_not_for_him():
    assert scout.classify(row("[Hiring] Logo designer for my app, $150",
                              "Need a logo and app icon set")) is None


def test_scam_signals_are_flagged_not_dropped():
    r = scout.classify(row("[Hiring] Simple python script, $500, I will pay you first via gift cards",
                           "Telegram only. I will pay upfront."))
    assert r and r["red_flags"] and r["score"] < 40


def test_hn_contract_comment_is_kept():
    r = scout.classify(row("Acme | AI Automation Engineer | REMOTE | Contract | $60-80/hr",
                           "We need someone to turn workflows into agent automations. Email jobs@acme.test",
                           sub="HN Who is hiring? (September 2026)", mode="all", source="hn"))
    assert r and r["tier"] == "explicit" and r["apply"] == "email" and r["amount_usd"] == 80


def test_listing_sub_without_marker_needs_hiring_words():
    r = scout.classify(row("Remote React Native developer needed, $40/hr, 20 hrs/week",
                           "We are hiring a contractor to ship our Expo app. Apply by DM.", sub="r/hiring", mode="all"))
    assert r and r["tier"] == "explicit"
