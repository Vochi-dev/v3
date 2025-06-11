import React from 'react';
import './NodePickerModal.css';
import { nodeDefinitions } from './node-definitions';

interface NodePickerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectNodeType: (nodeType: string) => void;
  sourceNodeId: string | null; // Принимаем ID исходного узла
}

const NodePickerModal: React.FC<NodePickerModalProps> = ({ isOpen, onClose, onSelectNodeType, sourceNodeId }) => {
  if (!isOpen) {
    return null;
  }
  
  // Здесь должна быть логика для определения доступных узлов на основе sourceNodeId
  // Пока что для упрощения покажем все, кроме "start"
  const availableNodes = Object.values(nodeDefinitions).filter(def => def.id !== 'start');


  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Выберите следующий узел</h2>
          <button onClick={onClose} className="modal-close-button">&times;</button>
        </div>
        <div className="modal-body">
          <div className="node-picker-options">
            {availableNodes.length > 0 ? (
                availableNodes.map(nodeDef => (
                    <button key={nodeDef.id} onClick={() => onSelectNodeType(nodeDef.id)}>
                        {nodeDef.label}
                    </button>
                ))
            ) : (
                <p>Для этого узла нет доступных следующих шагов.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default NodePickerModal; 