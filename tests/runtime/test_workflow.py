from synthetic_lab.demo.store import DemoStore
from synthetic_lab.runtime.workflow import milestones, findings


def test_workflow_requires_billing_and_cancellation_not_just_old_task():
    store = DemoStore()
    try:
        assert not any(milestones(store.workflow_snapshot()))
        store.create_account('account-1', 'test@example.test', 'test')
        store.create_project('account-1', 'project-1', 'Payments migration')
        store.create_task('project-1', 'task-1', 'Verify payment retry')
        store.complete_task('task-1')
        assert milestones(store.workflow_snapshot()) == [True, True, True, False, False, False]
        store.subscribe('account-1', 'subscription-1')
        store.purchase('account-1', 'purchase-1', 2500)
        assert not all(milestones(store.workflow_snapshot()))
        store.cancel_subscription('subscription-1')
        assert all(milestones(store.workflow_snapshot()))
        store.purchase('account-1', 'purchase-1', 2500)
        assert not all(milestones(store.workflow_snapshot()))
    finally:
        store.close()


def test_seeded_task_failure_does_not_advance_milestone():
    store = DemoStore(fault='task_completion_stale')
    try:
        store.create_account('account-1', 'test@example.test', 'test')
        store.create_project('account-1', 'project-1', 'Payments migration')
        store.create_task('project-1', 'task-1', 'Verify payment retry')
        store.complete_task('task-1')
        assert milestones(store.workflow_snapshot()) == [True, True, False, False, False, False]
        trace = [{'target': 'Complete task-1', 'status': 'success'}]
        assert findings(store.workflow_snapshot(), trace)[0]['invariant'] == 'task_completion'
        assert findings(store.workflow_snapshot(), []) == []
    finally:
        store.close()
