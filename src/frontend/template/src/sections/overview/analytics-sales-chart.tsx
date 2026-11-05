import type { CardProps } from '@mui/material/Card';

import { useState } from 'react';

import Box from '@mui/material/Box';
import Tab from '@mui/material/Tab';
import Card from '@mui/material/Card';
import Tabs from '@mui/material/Tabs';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import { alpha, useTheme } from '@mui/material/styles';

import { Chart, useChart } from 'src/components/chart';

// ----------------------------------------------------------------------

export type SalesRange = 'day' | 'week' | 'month' | 'year';
export type chartType = 'bar' | 'line';

export type ChartData = {
  categories: string[];
  type: chartType;
  series: { name: string; data: number[] }[];
};

type Props = CardProps & {
  title?: string;
  metrics?: {
    key: string;
    label: string;
    data: Record<SalesRange, ChartData>;
  }[];
};

const RANGES: { value: SalesRange; label: string }[] = [
  { value: 'day', label: 'All day' },
  { value: 'week', label: 'All week' },
  { value: 'month', label: 'All month' },
  { value: 'year', label: 'All year' },
];

export function AnalyticsSalesChart({
  title = 'Store Sales Trend',
  metrics = [],
  sx,
  ...other
}: Props) {
  const theme = useTheme();

  const [metricKey, setMetricKey] = useState(metrics[0]?.key);
  const [range, setRange] = useState<SalesRange>('year');

  const current = metrics.find((metric) => metric.key === metricKey) ?? metrics[0];
  const { categories, type, series} = current?.data[range] ?? { categories: [], series: [] };

  const main = theme.palette.primary.main;

  const chartOptions = useChart({
    colors:
    type === 'line'
      ? [theme.palette.primary.light, main, theme.palette.primary.dark]
      : [alpha(main, 0.3), main, alpha(main, 0.55)],
    stroke: { width: type === 'line' ? 3 : 0 },
    xaxis: { categories },
    plotOptions: { bar: { columnWidth: '45%', borderRadius: 0 } },
  });

  return (
    <Card
      sx={[
        {
          borderRadius: 0,
          boxShadow: 'none',
          border: `1px solid ${theme.palette.divider}`,
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
      {...other}
    >
      <Box
        sx={{
          px: 3,
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: `1px solid ${theme.palette.divider}`,
        }}
      >
        <Tabs value={current?.key ?? false} onChange={(_, value) => setMetricKey(value)}>
          {metrics.map((metric) => (
            <Tab key={metric.key} value={metric.key} label={metric.label} />
          ))}
        </Tabs>

        <Box sx={{ display: 'flex', gap: 0.5 }}>
          {RANGES.map((item) => (
            <Button
              key={item.value}
              size="small"
              color={range === item.value ? 'primary' : 'inherit'}
              onClick={() => setRange(item.value)}
              sx={{
                borderRadius: 0,
                color: range === item.value ? 'primary.main' : 'text.secondary',
              }}
            >
              {item.label}
            </Button>
          ))}
        </Box>
      </Box>

      <Typography variant="subtitle1" sx={{ px: 3, pt: 3 }}>
        {title}
      </Typography>
      <Chart
        type={type}
        series={series}
        options={chartOptions}
        sx={{ py: 2.5, pl: 1, pr: 2.5, height: 360 }}
      />
    </Card>
  );
}
