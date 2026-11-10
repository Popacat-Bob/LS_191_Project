import type { CardProps } from '@mui/material/Card';
 
import Card from '@mui/material/Card';
import { useTheme } from '@mui/material/styles';
import CardHeader from '@mui/material/CardHeader';
 
import { Chart, useChart } from 'src/components/chart';
 
// ----------------------------------------------------------------------
 
export type PieSection = {
  label: string;
  value: number; // percentage
};
 
type Props = CardProps & {
  title: string;
  subheader?: string;
  sections?: PieSection[];
};
 
export function AnalyticsPieChart({ title, subheader, sections = [], sx, ...other }: Props) {
  const theme = useTheme();
 
  const palette = [
    theme.palette.primary.main,
    theme.palette.info.main,
    theme.palette.success.main,
    theme.palette.warning.main,
    theme.palette.error.main,
  ];
 
  const chartOptions = useChart({
    labels: sections.map((section) => section.label),
    colors: sections.map((_, index) => palette[index % palette.length]),
    stroke: { width: 2, colors: [theme.palette.background.paper] },
    legend: { position: 'bottom' },
    tooltip: { y: { formatter: (value: number) => `${value}%` } },
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
      <CardHeader title={title} subheader={subheader} />
 
      <Chart
        type="pie"
        series={sections.map((section) => section.value)}
        options={chartOptions}
        sx={{ my: 3, mx: 'auto', width: 280, height: 320 }}
      />
    </Card>
  );
}
