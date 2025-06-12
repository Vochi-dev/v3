export interface SchemaData {
    nodes: Node[];
    edges: Edge[];
    viewport: { x: number; y: number; zoom: number };
}

export interface Schema {
    schema_id: string;
    enterprise_id: string;
    schema_name: string;
    schema_data: SchemaData;
    created_at: string;
}

export interface Line {
    id: string;
    display_name: string;
    in_schema: string | null;
}

// Добавим Node и Edge из reactflow, чтобы не импортировать их в каждом файле
import type { Node, Edge } from 'reactflow'; 