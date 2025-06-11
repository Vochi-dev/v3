export interface NodeDefinition {
  id: string;
  label: string;
  maxCount: number; // Use Infinity for no limit
  allowedSources: string[]; // List of node IDs that can precede this node
}

export const nodeDefinitions: NodeDefinition[] = [
  {
    id: '1',
    label: 'Поступил новый звонок',
    maxCount: 1,
    allowedSources: [], // Root node, cannot follow anything
  },
  {
    id: '2',
    label: 'График работы',
    maxCount: 1,
    allowedSources: ['1'],
  },
  {
    id: '3',
    label: 'Голосовое меню',
    maxCount: Infinity,
    allowedSources: ['1', '2', '4'],
  },
  {
    id: '4',
    label: 'Приветствие',
    maxCount: Infinity,
    allowedSources: ['1', '2', '3', '4', '5'],
  },
  {
    id: '5',
    label: 'Звонок на список',
    maxCount: Infinity,
    allowedSources: ['1', '2', '3', '4', '5'],
  },
];

export const findNodeDefinition = (id: string) => {
    return nodeDefinitions.find(def => def.id === id);
} 