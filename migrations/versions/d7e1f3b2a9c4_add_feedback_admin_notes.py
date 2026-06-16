"""add admin_notes to feedback

Revision ID: d7e1f3b2a9c4
Revises: c3f8e2a1d4b5
Create Date: 2026-06-16

"""
from alembic import op
import sqlalchemy as sa

revision = 'd7e1f3b2a9c4'
down_revision = 'c3f8e2a1d4b5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('feedback', sa.Column('admin_notes', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('feedback', 'admin_notes')
