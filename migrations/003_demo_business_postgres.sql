-- Business state for the synthetic SaaS application.
CREATE TABLE IF NOT EXISTS sul_demo_accounts (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password TEXT NOT NULL,
  trial_start TIMESTAMPTZ NOT NULL,
  trial_days INTEGER NOT NULL DEFAULT 7,
  plan TEXT NOT NULL DEFAULT 'free',
  onboarding_step INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sul_demo_projects (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES sul_demo_accounts(id),
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS sul_demo_tasks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES sul_demo_projects(id),
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  assignee TEXT
);
CREATE TABLE IF NOT EXISTS sul_demo_invitations (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES sul_demo_accounts(id),
  email TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS sul_demo_memberships (
  account_id TEXT NOT NULL REFERENCES sul_demo_accounts(id),
  member_id TEXT NOT NULL,
  role TEXT NOT NULL,
  PRIMARY KEY (account_id, member_id)
);
CREATE TABLE IF NOT EXISTS sul_demo_subscriptions (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES sul_demo_accounts(id),
  status TEXT NOT NULL DEFAULT 'active',
  started_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS sul_demo_invoices (
  id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL,
  account_id TEXT NOT NULL REFERENCES sul_demo_accounts(id),
  amount_cents INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS sul_demo_purchase_operations (
  operation_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES sul_demo_accounts(id),
  amount_cents INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS sul_demo_notifications (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES sul_demo_accounts(id),
  message TEXT NOT NULL,
  read BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL
);
