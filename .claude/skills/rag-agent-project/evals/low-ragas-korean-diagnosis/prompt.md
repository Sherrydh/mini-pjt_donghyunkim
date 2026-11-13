---
tags: [skill-creator]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

1차 평가 돌렸는데 RAGAS answer_relevancy가 0.41, context_precision이 0.53 나왔어. 답변은 전부 한국어고 ragas 0.4 버전 써. 그리고 LLM judge가 위키피디아 URL까지 단 답변을 '출처 신뢰 불가'라고 불통과 처리한 케이스도 2개 있어. 2차 평가까지 하루 남았는데 뭐부터 고쳐야 해?
