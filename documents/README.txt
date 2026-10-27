===============================================================================
 DEMO DOCUMENTS: what to upload, in which slot, in which order
===============================================================================

FILE NAMES TELL YOU THE UPLOAD SLOT
  1_SRS__...        -> the "Requirements SRS" slot
  2_API_V1__...     -> the "OpenAPI Spec v1" slot (the baseline API)
  3_API_V2__...     -> the "OpenAPI Spec v2" slot (the changed API, used for drift)
  REFERENCE_ONLY__  -> do NOT upload; open it on screen to compare
  (A file starting with "~$" is a Word lock file that exists while the
  document is open in Word. Ignore it.)

ONE PROJECT PER FOLDER
  A project holds one SRS, one v1 and one v2; uploading again replaces the old
  file. For each folder: Dashboard -> "+ New Project" -> name it after the
  folder -> card -> "Upload" -> drop the 3 files -> "Upload Files" ->
  "Looks good" -> open the project -> "Run Analysis" (first run 30-60 s).

BEFORE YOU START
  * Backend, frontend and Qdrant are running (start.txt, section 5).
  * For folder 03: the root .env has SRS_DIAGRAM_MODE=vision (plus
    OPENAI_API_KEY); restart uvicorn after adding it.


-------------------------------------------------------------------------------
STEP 1  01_main_demo_word_srs/          Project: "Nexus Store (Word SRS)"
-------------------------------------------------------------------------------
Upload  1_SRS__ecommerce_srs_ieee.docx  -> SRS
        2_API_V1__store_v1.json         -> API v1
        3_API_V2__store_v2.json         -> API v2   (a realistic, small update)

"Review extracted requirements", point at:
  - 10 requirements; chips "Word", "6 from requirement tables",
    "4 from tagged headings / paragraphs"
  - "Boilerplate sections removed": Revision History, Document Conventions,
    Definitions..., References, Appendix A: Glossary
  - Click REQ-01 -> Location "table 3 / row 2", Priority "High"
After Run Analysis:
  - Traceability: 100% . 10 requirements . 20 endpoints . 18 strong pairs;
    3D Graph / Heatmap / Split
  - Drift Analysis: 2 Minor drifts (REQ-09 and REQ-03, delta about -0.11),
    0 critical; compliance stays 100%. Click "Explain" on REQ-09.
    -> "Subtle drift: no test fails, but we still catch it."
  - Benchmarks: F1 0.71 vs TF-IDF 0.34, MAP 1.00
  - PR Bot -> Simulated: PASS / BLOCK REQ-04 / BLOCK REQ-09


-------------------------------------------------------------------------------
STEP 2  02_heavy_drift_breaking_release/  Project: "Nexus Store (breaking release)"
-------------------------------------------------------------------------------
Same SRS and same v1. Only v2 is different: a careless "v3.0 cleanup"
release that breaks six requirements at once.
Upload  1_SRS__ecommerce_srs_ieee.docx           -> SRS
        2_API_V1__store_v1.json                  -> API v1
        3_API_V2__store_v2_breaking_release.json -> API v2

What the breaking release changed (20 endpoints -> 14):
  REQ-09 2FA            /auth/2fa/challenge and /auth/2fa/verify REMOVED;
                        checkout no longer asks for two_fa_token
  REQ-07 Order tracking /orders/{id}/status and /orders/{id}/timeline REMOVED
  REQ-10 Notifications  GET and PUT /notifications/preferences REMOVED
  REQ-04 Refunds        client transaction_id replaced by a server reference
  REQ-06 Payments       card only (Apple Pay, Google Pay, saved methods dropped)
  REQ-01 Login          no JWT expiry, no session_id ("sessions are not tracked")

Run Analysis, then open "Drift Analysis". Expected (measured with the real
model; +/- 0.02 between machines):
  - Compliance v1 100%  ->  v2 60%   (delta -40%)
  - 58 drifted pairs: 6 CRITICAL, 26 Major, 26 Minor; all 10 requirements
    are touched
  - 4 requirements left uncovered in v2 (best score below 0.55):
      REQ-09 (0.38), REQ-10 (0.39), REQ-01 (0.45), REQ-07 (0.55)
  - The 6 Critical drifts (score falls to 0 because the endpoint is gone):
      REQ-07  GET  /orders/{order_id}/status        0.82 -> 0
      REQ-09  POST /auth/2fa/challenge              0.81 -> 0
      REQ-10  PUT  /notifications/preferences       0.73 -> 0
      REQ-09  POST /auth/2fa/verify                 0.72 -> 0
      REQ-10  GET  /notifications/preferences      0.69 -> 0
      REQ-07  GET  /orders/{order_id}/timeline      0.64 -> 0
  - Click "Explain" on a Critical REQ-09 card: the LLM says the 2FA endpoints
    were removed and how to restore them.
Talking point: "Step 1 was subtle drift. This is what a careless release looks
like: 40% of the business requirements silently lost, found in one click."
Note: the Major list also has knock-on pairs (a removed endpoint that was
loosely related to another requirement, e.g. REQ-05 vs the 2FA endpoints).
Focus the audience on the Critical cards and the uncovered requirements.
Compare side by side with the step 1 project to show the contrast.


-------------------------------------------------------------------------------
STEP 3  03_diagram_srs_uml/              Project: "Nexus Store (UML diagrams)"
-------------------------------------------------------------------------------
First open REFERENCE_ONLY__activity_diagram_REQ-09.png on screen and say:
"In this SRS, REQ-09 (two-factor authentication) is NOT written as text
anywhere. It exists only as this UML activity diagram."
Upload  1_SRS__Nexus_Store_SRS_v3.docx  -> SRS
        2_API_V1__store_v1.json         -> API v1
        3_API_V2__store_v2.json         -> API v2
"Review extracted requirements", point at:
  - 10 requirements: "6 from requirement tables", "3 from tagged headings",
    "1 read from diagrams"
    (Without SRS_DIAGRAM_MODE=vision you get 9: REQ-09 is missing. That makes
    a good "before / after" moment.)
  - "Read 2 diagrams" panel: FIG-1 system context (architecture, context
    only, no requirement) and FIG-2 activity diagram (created REQ-09)
  - REQ-09: "diagram" badge, Location "figure 2", and text with the facts
    from the picture: $500 or a new payment method, OTP valid 5 minutes,
    403 two_fa_required, two_fa_token
  - The appendix mock-up image was skipped (never sent to the AI)
After Run Analysis:
  - REQ-09 traces to POST /api/v1/auth/2fa/challenge (~0.85), /2fa/verify
    and /checkout
  - Drift Analysis flags REQ-09 in v2, so the diagram requirement is
    protected too


-------------------------------------------------------------------------------
GITHUB PART OF THE DEMO
-------------------------------------------------------------------------------
Issue texts and the step-by-step PR flow: start.txt, sections 10-13. For the
optional "diagram on GitHub" demo (start.txt, section 13), use
03_diagram_srs_uml/1_SRS__Nexus_Store_SRS_v3.docx as docs/SRS.docx in the
traceability-demo-store repo.
