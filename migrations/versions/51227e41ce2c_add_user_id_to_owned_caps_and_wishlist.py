"""add user_id to owned caps and wishlist

Revision ID: 51227e41ce2c
Revises: 4912ce54f243
Create Date: 2026-06-12 12:08:19.696506

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '51227e41ce2c'
down_revision = '4912ce54f243'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add user_id columns as nullable so existing rows can be backfilled
    with op.batch_alter_table('manual_caps', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))

    with op.batch_alter_table('user_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('user_items_product_id_fkey', 'products', ['product_id'], ['id'])

    # 2. Backfill existing rows to the current single user (sterling)
    op.execute("""
        UPDATE manual_caps
        SET user_id = (SELECT id FROM users WHERE username = 'sterling')
        WHERE user_id IS NULL
    """)
    op.execute("""
        UPDATE user_items
        SET user_id = (SELECT id FROM users WHERE username = 'sterling')
        WHERE user_id IS NULL
    """)

    # 3. Enforce NOT NULL and add FKs to users now that every row has a value
    with op.batch_alter_table('manual_caps', schema=None) as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key('manual_caps_user_id_fkey', 'users', ['user_id'], ['id'])

    with op.batch_alter_table('user_items', schema=None) as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key('user_items_user_id_fkey', 'users', ['user_id'], ['id'])

    # 4. Replace single-column PK on user_items with composite (product_id, user_id)
    #    so each user can have independent owned/wishlist/sold state per product
    op.drop_constraint('user_items_pkey', 'user_items', type_='primary')
    op.create_primary_key('user_items_pkey', 'user_items', ['product_id', 'user_id'])


def downgrade():
    op.drop_constraint('user_items_pkey', 'user_items', type_='primary')
    op.create_primary_key('user_items_pkey', 'user_items', ['product_id'])

    with op.batch_alter_table('user_items', schema=None) as batch_op:
        batch_op.drop_constraint('user_items_user_id_fkey', type_='foreignkey')
        batch_op.drop_constraint('user_items_product_id_fkey', type_='foreignkey')
        batch_op.drop_column('user_id')

    with op.batch_alter_table('manual_caps', schema=None) as batch_op:
        batch_op.drop_constraint('manual_caps_user_id_fkey', type_='foreignkey')
        batch_op.drop_column('user_id')
