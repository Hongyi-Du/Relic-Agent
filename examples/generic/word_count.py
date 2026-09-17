"""A trusted plugin with no network or filesystem dependencies."""


def execute(arguments, context):
    count = len(arguments['text'].split())
    return {'word_count': count, 'artifacts': [
        {'id': 'word-count-report', 'title': 'Word count report',
         'content': f'Counted {count} words for {context["agent_id"]}.',
         'task_ids': ['research-note']}
    ]}
