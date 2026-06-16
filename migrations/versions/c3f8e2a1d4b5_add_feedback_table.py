"""add feedback table

Revision ID: c3f8e2a1d4b5
Revises: b1c4a9e7f3d2
Create Date: 2026-06-16

"""
from alembic import op
import sqlalchemy as sa

revision = 'c3f8e2a1d4b5'
down_revision = 'b1c4a9e7f3d2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'feedback',
        sa.Column('id',         sa.Integer(),  primary_key=True, autoincrement=True, nullable=False),
        sa.Column('user_id',    sa.Integer(),  sa.ForeignKey('users.id'), nullable=False),
        sa.Column('type',       sa.Text(),     nullable=False),
        sa.Column('title',      sa.Text(),     nullable=False),
        sa.Column('body',       sa.Text(),     nullable=True),
        sa.Column('status',     sa.Text(),     nullable=False, server_default='open'),
        sa.Column('created_at', sa.Text(),     nullable=False),
    )
    op.create_index('ix_feedback_status', 'feedback', ['status'])
    op.create_index('ix_feedback_user_id', 'feedback', ['user_id'])


def downgrade():
    op.drop_index('ix_feedback_user_id', table_name='feedback')
    op.drop_index('ix_feedback_status', table_name='feedback')
    op.drop_table('feedback')
