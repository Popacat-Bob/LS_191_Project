import { useState } from 'react';

import Grid from '@mui/material/Grid';
import Stack from '@mui/material/Stack';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogTitle from '@mui/material/DialogTitle';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import { SelectChangeEvent } from '@mui/material/Select';
import DialogContentText from '@mui/material/DialogContentText';

import { DashboardContent } from 'src/layouts/dashboard';

import { ALL_FILTERS } from '../utils';
import { FilterSelect } from '../order-filter';
import { OrderSearch } from '../order-searchbar';
import { OrderAddDialog } from '../order-add-button';
import { OrderTable, EditField } from '../order-table';
import { OrderEditDialog } from '../order-edit-dialogue';
import { OrderProps, OrderStatus, FilterStatus } from '../utils';

// ----------------------------------------------------------------------

// TODO: replace with data fetched from the backend
const INITIAL_ORDERS: OrderProps[] = [
  { id: 'ORD-1001', customer: 'Maria Santos', item: 'Floral Dress', amount: 1250, status: OrderStatus.Pending, date: '2026-11-02' },
  { id: 'ORD-1002', customer: 'John Reyes', item: 'Linen Shirt', amount: 890, status: OrderStatus.Processing, date: '2026-11-03' },
  { id: 'ORD-1003', customer: 'Ana Cruz', item: 'Denim Romper', amount: 1580, status: OrderStatus.Shipped, date: '2026-11-04' },
  { id: 'ORD-1004', customer: 'Luis Garcia', item: 'Summer Dress', amount: 1100, status: OrderStatus.Delivered, date: '2026-11-05' },
  { id: 'ORD-1005', customer: 'Carla Lim', item: 'Cotton Shirt', amount: 650, status: OrderStatus.Cancelled, date: '2026-11-06' },
  { id: 'ORD-1006', customer: 'Paolo Tan', item: 'Floral Romper', amount: 1400, status: OrderStatus.Pending, date: '2026-11-07' },
];

const nextOrderId = (orders: OrderProps[]) => {
  const max = orders.reduce((acc, o) => {
    const n = Number(o.id.replace('ORD-', ''));
    return Number.isNaN(n) ? acc : Math.max(acc, n);
  }, 1000);
  return `ORD-${max + 1}`;
};

export function OrdersView() {
  const [search, setSearch] = useState<string>('');
  const [filter, setFilter] = useState<number>(FilterStatus.None);
  const [orders, setOrders] = useState<OrderProps[]>(INITIAL_ORDERS);
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<{ order: OrderProps; field: EditField } | null>(null);
  const [removeTarget, setRemoveTarget] = useState<OrderProps | null>(null);

  const lsearch = search.trim().toLowerCase();

  const visibleOrders = orders.filter((order) => {
    const inSearch = [order.id, order.customer, order.item].some((field) =>
      field.toLowerCase().includes(lsearch)
    );
    const inFilter = filter === FilterStatus.None || (order.status as number) === filter;

    return inSearch && inFilter;
  });

  const handleFilter = (event: SelectChangeEvent<number>) => {
    setFilter(event.target.value as number);
  };

  const handleAddOrder = (data: Omit<OrderProps, 'id'>) => {
    setOrders((prev) => [{ ...data, id: nextOrderId(prev) }, ...prev]);
  };

  const handleEditOrder = (id: string, patch: Partial<Omit<OrderProps, 'id'>>) => {
    setOrders((prev) => prev.map((o) => (o.id === id ? { ...o, ...patch } : o)));
  };

  const handleConfirmRemove = () => {
    if (removeTarget) {
      setOrders((prev) => prev.filter((o) => o.id !== removeTarget.id));
    }
    setRemoveTarget(null);
  };

  return (
    <DashboardContent maxWidth="xl">
      <Stack spacing={3}>
        <Grid container spacing={2} alignItems="center">
          <Grid size={{ xs: 12, sm: 6, md: 7 }}>
            <OrderSearch value={search} onChange={setSearch} />
          </Grid>

          <Grid size={{ xs: 6, sm: 3, md: 3 }}>
            <FilterSelect
              labelId="status-filter"
              id="f-000"
              value={filter}
              onChange={handleFilter}
              options={ALL_FILTERS}
            />
          </Grid>

          <Grid size={{ xs: 6, sm: 3, md: 2 }}>
            <Button fullWidth variant="contained" onClick={() => setAddOpen(true)}>
              Add order
            </Button>
          </Grid>
        </Grid>

        <Grid container spacing={2}>
          <OrderTable
            rows={visibleOrders}
            onEdit={(order, field) => setEditTarget({ order, field })}
            onRemove={setRemoveTarget}
          />
        </Grid>
      </Stack>

      <OrderAddDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSubmit={handleAddOrder}
      />

      <OrderEditDialog
        open={!!editTarget}
        order={editTarget?.order ?? null}
        field={editTarget?.field ?? null}
        onClose={() => setEditTarget(null)}
        onSubmit={handleEditOrder}
      />

      <Dialog open={!!removeTarget} onClose={() => setRemoveTarget(null)}>
        <DialogTitle>Remove order?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {removeTarget
              ? `${removeTarget.id} (${removeTarget.customer}) will be permanently removed.`
              : ''}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRemoveTarget(null)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={handleConfirmRemove}>
            Remove
          </Button>
        </DialogActions>
      </Dialog>
    </DashboardContent>
  );
}
