# Misleading Claims Analysis: TikTok AI Video Workflow

This document flags exaggerated or misleading claims commonly found in AI-powered TikTok automation pitches.

---

## 1. "$3/video at 550 videos/day" — Misleading Pricing

**Claim:** You can generate 550 videos/day at $3/video using Arcads.

**Reality:**
- Arcads standard plans run ~$110/month for 10 videos and ~$220/month for 20 videos — roughly **$11/video** on the Starter plan.
- To reach $3/video at 550/day (≥16,500 videos/month), you would need a heavily negotiated **enterprise/custom contract at significant volume**.
- This is not something you simply sign up for. The $3 figure is not a publicly available rate.

---

## 2. "550 Videos/Day" — Misleading Technical Simplicity

**Claim:** Generating 550 videos/day is achievable with a simple setup (e.g., Zapier).

**Reality:**
- Video generation takes up to 10 minutes per video. Even at an optimistic 2 minutes/video:
  - 550 videos × 2 min = **1,100 minutes (~18 hours)** of continuous rendering per day.
- Achieving this requires **parallelized API calls across concurrent jobs**, not a simple Zapier workflow.
- A proper implementation demands **async batch processing** infrastructure.

---

## 3. "Phone Farm" Distribution — Significant Red Flag

**Reference:** maverickcreative.net is cited for this method.

**Reality:**
- Running hundreds of fake or semi-fake social media accounts violates the **Terms of Service of every major platform** (TikTok, Instagram, YouTube, etc.).
- Platform detection of coordinated inauthentic behavior is increasingly sophisticated.
- This is the part of the workflow most likely to result in **account bans**, **legal exposure**, or worse.
- It is notably buried at **step 7 of 9** in the workflow description — after the reader is already invested.

---

## 4. "Claude Cowork" — Misleading Branding

**Claim:** The workflow uses a special "Claude Cowork" feature.

**Reality:**
- "Claude Cowork" is not a real product or feature.
- This is simply the **Claude API with a structured system prompt** designed to make Claude act as a creative strategist.
- Any developer can replicate this with a standard API call. There is no proprietary "Cowork" capability.
