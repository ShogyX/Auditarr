/**
 * ``components/ui`` barrel.
 *
 * Stage 1 adds: ``Page``, ``Input``, ``Select``, ``Textarea``, ``Switch``,
 * ``Modal``, ``Drawer``, ``Tabs``, ``Toolbar``, ``FilterBar``, ``DataGrid``,
 * ``Metric``, ``Segmented``. Together with the pre-existing ``Button``,
 * ``Card``, ``Pill``, ``Icon``, ``Sparkline``, ``SeverityHeatmap``, and
 * ``States``, this is the canonical primitive set for all feature pages.
 *
 * All feature pages should import exclusively from this barrel.
 * Feature-local shadow primitives are deprecated.
 */

export { Button, buttonVariants, type ButtonProps } from "./Button";
export { Card, CardBody, CardBodyFlush, CardHead } from "./Card";
export { DataGrid, type DataGridDensity, type DataGridProps } from "./DataGrid";
export {
  Drawer,
  DrawerBody,
  DrawerFoot,
  DrawerHead,
  type DrawerHeadProps,
  type DrawerProps,
} from "./Drawer";
export { EmptyState, ErrorState, LoadingState } from "./States";
export { Field, type FieldProps } from "./Field";
export { Icon, type IconName } from "./Icon";
export { Input, type InputProps } from "./Input";
export { Metric, type MetricDelta, type MetricProps } from "./Metric";
export {
  Modal,
  ModalBody,
  ModalFoot,
  ModalHead,
  type ModalProps,
  type ModalSize,
} from "./Modal";
export { Page, type PageProps } from "./Page";
export { Pill, Tag } from "./Pill";
export { Segmented, type SegmentedOption, type SegmentedProps } from "./Segmented";
export { Select, type SelectProps } from "./Select";
export { SeverityHeatmap } from "./SeverityHeatmap";
export { Sparkline } from "./Sparkline";
export { Switch, type SwitchProps } from "./Switch";
export { Tabs, TabsPanel, type TabItem, type TabsProps } from "./Tabs";
export { Textarea, type TextareaProps } from "./Textarea";
export { FilterBar, Toolbar, type FilterChip, type ToolbarProps } from "./Toolbar";
