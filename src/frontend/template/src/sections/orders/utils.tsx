export enum OrderStatus {
    Pending,
    Processing,
    Shipped,
    Delivered,
    Cancelled,
}

export const OS_LABEL: Record<OrderStatus, string> = {
  [OrderStatus.Pending]: 'Pending',
  [OrderStatus.Processing]: 'Processing',
  [OrderStatus.Shipped]: 'Shipped',
  [OrderStatus.Delivered]: 'Delivered',
  [OrderStatus.Cancelled]: 'Cancelled',
};

export enum FilterStatus {
    Pending,
    Processing,
    Shipped,
    Delivered,
    Cancelled,
    None,
}

export type FilterProps = {
    status: FilterStatus;
}

export const ALL_FILTERS: FilterProps[] = [
  { status: FilterStatus.None },
  { status: FilterStatus.Pending },
  { status: FilterStatus.Processing },
  { status: FilterStatus.Shipped },
  { status: FilterStatus.Delivered },
  { status: FilterStatus.Cancelled },
];

export const FS_LABEL: Record<FilterStatus, string> = {
  [FilterStatus.None]: 'All',
  [FilterStatus.Pending]: 'Pending',
  [FilterStatus.Processing]: 'Processing',
  [FilterStatus.Shipped]: 'Shipped',
  [FilterStatus.Delivered]: 'Delivered',
  [FilterStatus.Cancelled]: 'Cancelled',
};

export type OrderProps = {
  id: string;
  customer: string;
  item: string;
  amount: number;
  status: OrderStatus;
  date: string;
};


