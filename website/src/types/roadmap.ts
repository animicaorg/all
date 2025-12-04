export type Milestone = {
  id: string;
  title: string;
  status: "NOW" | "NEXT" | "LATER" | "DONE";
  quarter?: string;
  summary?: string;
  progress?: number;
  tags?: string[];
  links?: { label: string; href: string }[];
};
