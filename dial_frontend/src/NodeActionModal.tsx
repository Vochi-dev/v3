import React from 'react';
import './NodeActionModal.css';
import { Node } from 'reactflow';
import { nodeRules, NodeType } from './nodeRules';

interface NodeActionModalProps {
    sourceNodeType: NodeType;
    nodes: Node[];
    onClose: () => void;
    onDialClick: () => void;
    onGreetingClick: () => void;
    onWorkScheduleClick: () => void;
    // onIVRClick: () => void; // Добавим, когда будет готово
}

const NodeActionModal: React.FC<NodeActionModalProps> = ({ 
    sourceNodeType, 
    nodes,
    onClose, 
    onDialClick, 
    onGreetingClick, 
    onWorkScheduleClick 
}) => {
    
    const nodeCounts = nodes.reduce((acc, node) => {
        const type = node.type as NodeType;
        if (type) {
            acc[type] = (acc[type] || 0) + 1;
        }
        return acc;
    }, {} as Record<NodeType, number>);

    const isAllowed = (nodeType: NodeType) => {
        const rule = nodeRules.find(r => r.type === nodeType);
        if (!rule) return false;

        // Проверка, может ли этот узел следовать за исходным
        if (!rule.allowedSources.includes(sourceNodeType)) {
            return false;
        }

        // Проверка на максимальное количество
        if (rule.maxInstances > 0 && (nodeCounts[nodeType] || 0) >= rule.maxInstances) {
            return false;
        }

        return true;
    };
    
    return (
        <div className="node-action-modal-overlay" onClick={onClose}>
            <div className="node-action-modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="node-action-modal-header">
                    <h4>Выберите следующее действие</h4>
                    <button onClick={onClose} className="close-button">&times;</button>
                </div>
                <div className="node-action-modal-body">
                    {isAllowed(NodeType.Dial) && <button className="action-button" onClick={onDialClick}>Звонок на список</button>}
                    {isAllowed(NodeType.Greeting) && <button className="action-button" onClick={onGreetingClick}>Приветствие</button>}
                    {isAllowed(NodeType.IVR) && <button className="action-button" disabled>Голосовое меню</button>}
                    {isAllowed(NodeType.WorkSchedule) && <button className="action-button" onClick={onWorkScheduleClick}>График работы</button>}
                </div>
            </div>
        </div>
    );
};

export default NodeActionModal; 