// node-definitions.ts

export interface NodeOption {
    type: string;
    label: string;
}

export const NODE_TYPES: { [key: string]: { label: string; allowedNext: string[] } } = {
    'incoming-call': {
        label: 'Поступил новый звонок',
        allowedNext: ['play-sound', 'menu', 'wait-for-answer', 'hangup'],
    },
    'play-sound': {
        label: 'Проиграть звук',
        allowedNext: ['menu', 'hangup', 'transfer-to-gsm'],
    },
    'menu': {
        label: 'Меню',
        allowedNext: ['play-sound', 'hangup', 'transfer-to-gsm', 'get-user-input'],
    },
    'wait-for-answer': {
        label: 'Ожидать ответа',
        allowedNext: ['play-sound', 'hangup', 'transfer-to-gsm'],
    },
    'hangup': {
        label: 'Положить трубку',
        allowedNext: [],
    },
    'transfer-to-gsm': {
        label: 'Перевод на GSM',
        allowedNext: ['hangup'],
    },
    'get-user-input': {
        label: 'Получить ввод пользователя',
        allowedNext: ['play-sound', 'hangup', 'transfer-to-gsm'],
    },
};

export const getNodeOptions = (sourceType: string): NodeOption[] => {
    const allowed = NODE_TYPES[sourceType]?.allowedNext || [];
    return allowed.map(type => ({
        type,
        label: NODE_TYPES[type].label,
    }));
}; 