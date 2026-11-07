"""Explicit demonstration strategy; not a replacement for a bank's SPR."""
import math

def decide(score, stops, cutoff, direction='le'):
 if any(v is True for v in stops):return 'REJECT','STOP_FACTOR'
 if score is None or any(v is None for v in stops):return 'UNKNOWN','INCOMPLETE'
 if direction not in ('le','ge'):raise ValueError('direction must be le or ge')
 score=float(score);cutoff=float(cutoff)
 if not math.isfinite(score) or not math.isfinite(cutoff):raise ValueError('Non-finite score or cutoff')
 return ('APPROVE','SCORE_PASS') if (score<=cutoff if direction=='le' else score>=cutoff) else ('REJECT','SCORE_CUTOFF')
