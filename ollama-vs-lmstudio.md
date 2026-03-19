============================================================
FULL PRO MODE PERFORMANCE REPORT
============================================================
Backend:              LM Studio
Model:
Total Emails Tested:  500
Fast Mode Accuracy:   100.00% (500/500)
Pure AI Accuracy:     100.00% (500/500)
PRO MODE (Mixed):    100.00% (500/500)
------------------------------------------------------------
AI Calls avoided:     299 (saved by Fast Mode TUT)
AI Calls made:        201
AI Rescued (FP->TUT): 0 (Fast would have deleted unfairly)
AI Missed (FP->SIL):  0 (Both Fast & AI wanted to delete)
AI Broke (SIL->TUT):  0 (AI kept what should be deleted)
------------------------------------------------------------
Confusion Matrix (Pro Mode, positive=SIL):
  TP (SIL correct):   201
  FP (SIL wrong):     0
  TN (TUT correct):   299
  FN (TUT wrong):     0
  Precision:          1.0000
  Recall:             1.0000
  F1 Score:           1.0000
------------------------------------------------------------
AI Latency (per email):
  Avg:  0.62s
  Min:  0.00s
  Max:  3.68s
  P95:  1.84s
------------------------------------------------------------
Total Time:           39.66s
============================================================



============================================================
FULL PRO MODE PERFORMANCE REPORT
============================================================
Backend:              Ollama
Model:                qwen3.5:0.8B
Total Emails Tested:  500
Fast Mode Accuracy:   100.00% (500/500)
Pure AI Accuracy:     100.00% (500/500)
PRO MODE (Mixed):    100.00% (500/500)
------------------------------------------------------------
AI Calls avoided:     299 (saved by Fast Mode TUT)
AI Calls made:        201
AI Rescued (FP->TUT): 0 (Fast would have deleted unfairly)
AI Missed (FP->SIL):  0 (Both Fast & AI wanted to delete)
AI Broke (SIL->TUT):  0 (AI kept what should be deleted)
------------------------------------------------------------
Confusion Matrix (Pro Mode, positive=SIL):
  TP (SIL correct):   201
  FP (SIL wrong):     0
  TN (TUT correct):   299
  FN (TUT wrong):     0
  Precision:          1.0000
  Recall:             1.0000
  F1 Score:           1.0000
------------------------------------------------------------
AI Latency (per email):
  Avg:  1.79s
  Min:  0.00s
  Max:  8.24s
  P95:  4.92s
------------------------------------------------------------
Total Time:           76.99s
============================================================