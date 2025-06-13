export enum NodeType {
    Start = 'custom', // Matches the existing type for IncomingCallNode
    Greeting = 'greeting',
    Dial = 'dial',
    WorkSchedule = 'workSchedule',
    IVR = 'ivr',
}

export interface NodeRule {
    type: NodeType;
    name: string;
    maxInstances: number; // 0 for unlimited
    allowedSources: NodeType[];
}

// Based on the provided table:
// 1. Поступил новый звонок (Start)
// 2. График работы (WorkSchedule)
// 3. Голосовое меню (IVR)
// 4. Приветствие (Greeting)
// 5. Звонок на список (Dial)

export const nodeRules: NodeRule[] = [
    {
        type: NodeType.Start,
        name: 'Поступил новый звонок',
        maxInstances: 1,
        allowedSources: [], // Is a root node
    },
    {
        type: NodeType.WorkSchedule,
        name: 'График работы',
        maxInstances: 1,
        allowedSources: [NodeType.Start], // Follows 1
    },
    {
        type: NodeType.IVR,
        name: 'Голосовое меню',
        maxInstances: 0, // Без огр
        allowedSources: [NodeType.Start, NodeType.WorkSchedule, NodeType.Greeting], // Follows 1, 2, 4
    },
    {
        type: NodeType.Greeting,
        name: 'Приветствие',
        maxInstances: 0, // Без огр
        allowedSources: [NodeType.Start, NodeType.WorkSchedule, NodeType.IVR, NodeType.Greeting, NodeType.Dial], // Follows 1, 2, 3, 4, 5
    },
    {
        type: NodeType.Dial,
        name: 'Звонок на список',
        maxInstances: 0, // Без огр
        allowedSources: [NodeType.Start, NodeType.WorkSchedule, NodeType.IVR, NodeType.Greeting, NodeType.Dial], // Follows 1, 2, 3, 4, 5
    },
];

export const getNodeRule = (type: NodeType): NodeRule | undefined => {
    return nodeRules.find(rule => rule.type === type);
}; 