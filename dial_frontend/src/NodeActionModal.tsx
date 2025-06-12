import React from 'react';
import './NodeActionModal.css';

interface NodeActionModalProps {
    onClose: () => void;
}

const NodeActionModal: React.FC<NodeActionModalProps> = ({ onClose }) => {
    return (
        <div className="node-action-modal-overlay" onClick={onClose}>
            <div className="node-action-modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="node-action-modal-header">
                    <h4>Выберите следующее действие</h4>
                    <button onClick={onClose} className="close-button">&times;</button>
                </div>
                <div className="node-action-modal-body">
                    <button className="action-button">Звонок на список</button>
                    <button className="action-button">Приветствие</button>
                    <button className="action-button">Голосовое меню</button>
                    <button className="action-button">График работы</button>
                </div>
            </div>
        </div>
    );
};

export default NodeActionModal; 