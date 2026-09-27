# Device 4 — Hands (compile-ladder)
# NL → Ladder-Logic compiler for the "Bob on the Floor" pipeline.
#
# Pipeline position:  Memory (Schema 3) → Hands (Schema 4) → Approval
#
# Supported PLC dialect: IEC-61131-3 Structured Text / ASCII Ladder
# Supported instruction patterns:
#   1. Start motor
#   2. Stop motor
#   3. Threshold-based stop  ("if X exceeds threshold, stop Y")
#   4. Threshold-based start ("if X below threshold, start Y")
#   5. Timer-based delayed stop
#   6. Timer-based delayed start
#   7. Conditional interlock (stop A when B is running)
#   8. Emergency stop (E-stop pattern)
