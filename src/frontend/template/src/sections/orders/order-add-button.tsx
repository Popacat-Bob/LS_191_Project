import { useState } from 'react';

import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import MenuItem from '@mui/material/MenuItem';
import TextField from '@mui/material/TextField';
import DialogTitle from '@mui/material/DialogTitle';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';

import { OS_LABEL, OrderProps, OrderStatus } from './utils';

// ----------------------------------------------------------------------

const STATUS_OPTIONS = [
  OrderStatus.Pending,
  OrderStatus.Processing,
  OrderStatus.Shipped,
  OrderStatus.Delivered,
  OrderStatus.Cancelled,
].map((value) => ({ value, label: OS_LABEL[value] }));

const today = () => new Date().toISOString().slice(0, 10);

type FormState = {
  customer: string;
  item: string;
  amount: string;
  status: OrderStatus;
  date: string;
};

type FormErrors = Partial<Record<keyof FormState, string>>;

const emptyForm = (): FormState => ({
  customer: '',
  item: '',
  amount: '',
  status: OrderStatus.Pending,
  date: today(),
});

type Props = {
  open: boolean;
  onClose: () => void;
  onSubmit: (order: Omit<OrderProps, 'id'>) => void;
};

export function OrderAddDialog({ open, onClose, onSubmit }: Props) {
  const [form, setForm] = useState<FormState>(emptyForm());
  const [errors, setErrors] = useState<FormErrors>({});

  const handleChange =
    (field: 'customer' | 'item' | 'amount' | 'date') =>
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setForm((prev) => ({ ...prev, [field]: e.target.value }));
      setErrors((prev) => ({ ...prev, [field]: undefined }));
    };

  const handleStatusChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm((prev) => ({ ...prev, status: Number(e.target.value) as OrderStatus }));
  };

  const validate = () => {
    const next: FormErrors = {};
    const amount = Number(form.amount);

    if (!form.customer.trim()) next.customer = 'Customer is required';
    if (!form.item.trim()) next.item = 'Item is required';
    if (!form.amount.trim() || Number.isNaN(amount) || amount <= 0) {
      next.amount = 'Enter an amount greater than 0';
    }
    if (!form.date) next.date = 'Date is required';

    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleClose = () => {
    setForm(emptyForm());
    setErrors({});
    onClose();
  };

  const handleSave = () => {
    if (!validate()) return;

    onSubmit({
      customer: form.customer.trim(),
      item: form.item.trim(),
      amount: Number(form.amount),
      status: form.status,
      date: form.date,
    });
    handleClose();
  };

  return (
    <Dialog open={open} onClose={handleClose} fullWidth maxWidth="sm">
      <DialogTitle>Add order</DialogTitle>

      <DialogContent
        sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '8px !important' }}
      >
        <TextField
          label="Customer"
          value={form.customer}
          onChange={handleChange('customer')}
          error={!!errors.customer}
          helperText={errors.customer}
          fullWidth
        />
        <TextField
          label="Item"
          value={form.item}
          onChange={handleChange('item')}
          error={!!errors.item}
          helperText={errors.item}
          fullWidth
        />
        <TextField
          label="Amount"
          type="number"
          value={form.amount}
          onChange={handleChange('amount')}
          error={!!errors.amount}
          helperText={errors.amount}
          slotProps={{ htmlInput: { min: 0, step: '0.01' } }}
          fullWidth
        />
        <TextField
          select
          label="Status"
          value={form.status}
          onChange={handleStatusChange}
          fullWidth
        >
          {STATUS_OPTIONS.map((opt) => (
            <MenuItem key={opt.value} value={opt.value}>
              {opt.label}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          label="Date"
          type="date"
          value={form.date}
          onChange={handleChange('date')}
          error={!!errors.date}
          helperText={errors.date}
          slotProps={{ inputLabel: { shrink: true } }}
          fullWidth
        />
      </DialogContent>

      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button variant="contained" onClick={handleSave}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}
