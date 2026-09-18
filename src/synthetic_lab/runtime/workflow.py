"""Database-verified milestones for the controlled SaaS evaluation."""
from synthetic_lab.memory.context import MemoryContextAssembler


def milestones(snapshot):
    return [snapshot['accounts'] == 1, snapshot['projects'] == 1,
            snapshot['task'] == 'completed', snapshot['subscription'] in ('active', 'cancelled'),
            snapshot['charges'] == 1 and snapshot['total'] == 2500,
            snapshot['subscription'] == 'cancelled']


def findings(snapshot, trace):
    """Compare attempted business actions to authoritative post-run state."""
    result = []
    checks = [('Complete task-1', 'task_completion', 'completed', snapshot['task']),
              ('Cancel subscription', 'subscription_cancellation', 'cancelled', snapshot['subscription'])]
    for target, invariant, expected, actual in checks:
        evidence = [i for i, action in enumerate(trace) if action['target'] == target and action['status'] == 'success']
        if evidence and actual != expected:
            result.append(dict(invariant=invariant, expected=expected, actual=actual, action_indices=evidence))
    charges = [i for i, action in enumerate(trace) if action['target'] == 'Charge account' and action['status'] == 'success']
    if len(charges) == 1 and (snapshot['charges'] != 1 or snapshot['total'] != 2500):
        result.append(dict(invariant='single_charge', expected={'charges': 1, 'total': 2500}, actual={'charges': snapshot['charges'], 'total': snapshot['total']}, action_indices=charges))
    return result


class WorkflowContext(MemoryContextAssembler):
    def __init__(self, repository, *, store, tool_registry):
        super().__init__(repository, tool_registry=tool_registry)
        self.store = store

    async def build(self, persona, session, observation, budgets):
        snapshot = self.store.workflow_snapshot()
        done = milestones(snapshot)
        goals = [
            'Create an account using email workflow@example.test and password workflow-password. Fill empty required fields, then submit Create account.',
            'Open Projects from the dashboard. Create one project named Payments migration using Create project.',
            'Open Tasks. If no task exists, fill the title with Verify payment retry then click Create task. If the task is pending, click Complete task-1 once.',
            'Open Billing and click Start subscription once.',
            'Open Billing and click Charge account once for 2500 cents. Do not charge again.',
            'Open Billing and click Cancel subscription once.',
        ]
        index = next((i for i, complete in enumerate(done) if not complete), len(goals))
        goal = goals[index] if index < len(goals) else 'All milestones verified. Return kind=finish with a summary.'
        if index == 2:
            goal = ('Open Tasks. Fill the title Verify payment retry and submit Create task. The task does not exist yet.' if snapshot['task'] is None else 'Open Tasks and click Complete task-1. The task has been created and is pending. Do not create or fill another task.')
        scoped = persona.model_copy(update={'goal': f'Current milestone {index + 1}: {goal} Completed milestones: {[i+1 for i,v in enumerate(done) if v]}. Observed business state: {snapshot}. Work only on the current milestone. Return action decisions until all milestones are verified.'})
        return await super().build(scoped, session, observation, budgets)
