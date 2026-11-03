"""Shared conservative classification; the local executor remains authoritative."""
READ_ACTIONS={'search','read_pdf','read_draft','read_context','ask_user','chat_reply'}

def action_writes(action):
    if action.get('action')=='tool_call':
        tool_id=action.get('tool_id');payload=action.get('input') or {}
        if tool_id in {'file_import','fill_docx_template'}: return True
        if tool_id in {'formula_check','diagram_generate','chart_generate'} and (payload.get('save') or payload.get('output_filename')): return True
        from paperagent.tools.registry import REGISTRY
        tool=REGISTRY.get(action.get('tool_id'))
        return tool is None or tool.permission not in {'local_read','network_read','notify_in_app'}
    return action.get('action') not in READ_ACTIONS
