"""Bound cloud observations without sending entire device outputs repeatedly."""
import copy
from paperagent.harness.context_budget import estimate_tokens,ContextBudgetExceeded
from paperagent.product_config import load


def fit_cloud_observation(observation):
    value=copy.deepcopy(observation)
    budget=load().budget.context_input_tokens
    removed=0
    for key in ('method_results','results'):
        while estimate_tokens(value)>budget and len(value.get(key,[]))>1:
            value[key].pop(0);removed+=1
    if estimate_tokens(value)>budget:
        # All source content is still retained in the task event/state store.
        for key in ('method_results','results'):
            for i,item in enumerate(value.get(key,[])):
                import json
                raw=json.dumps(item,ensure_ascii=False)
                if len(raw)>6000:
                    value[key][i]={'excerpt':raw[:6000],'truncated':True,'note':'Stored result omitted from model context; do not infer missing content'}
    if estimate_tokens(value)>budget:
        value['memories']={};removed+=1
    value['context_diagnostics']={'removed_results':removed,'input_budget':budget}
    if estimate_tokens(value)>budget:
        raise ContextBudgetExceeded('Cloud request and protected context exceed input budget')
    return value
