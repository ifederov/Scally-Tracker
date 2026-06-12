"""add performance indexes for products listing

Revision ID: a7c4e1f9d2b3
Revises: f58dd380d17f
Create Date: 2026-06-12 13:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c4e1f9d2b3'
down_revision = 'f58dd380d17f'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_variants_product_id', 'variants', ['product_id'])
    op.create_index('ix_alerts_type_created_at', 'alerts', ['alert_type', 'created_at'])
    op.create_index('ix_products_category', 'products', ['category'])


def downgrade():
    op.drop_index('ix_products_category', table_name='products')
    op.drop_index('ix_alerts_type_created_at', table_name='alerts')
    op.drop_index('ix_variants_product_id', table_name='variants')
