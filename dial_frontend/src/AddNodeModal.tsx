import React, { useRef, useEffect } from 'react';
import './AddNodeModal.css';

interface AddNodeModalProps {
  onClose: () => void;
  // onAddNode: (nodeType: string) => void; // Мы добавим это позже
}

const AddNodeModal: React.FC<AddNodeModalProps> = ({ onClose }) => {
  const modalRef = useRef<HTMLDivElement>(null);

  // Закрытие по клику вне окна
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (modalRef.current && !modalRef.current.contains(event.target as Node)) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [onClose]);

  // Пока что кнопки ничего не делают, кроме вывода в консоль
  const handleAddNodeClick = (nodeType: string) => {
    console.log(`Add node of type: ${nodeType}`);
    // onClose(); // Можно закрывать модалку после выбора
  };

  return (
    <div className="add-node-modal-overlay">
      <div className="add-node-modal-content" ref={modalRef}>
        <header className="add-node-modal-header">
          <h3>Добавить элемент</h3>
          <button onClick={onClose} className="add-node-modal-close-button">&times;</button>
        </header>
        <main className="add-node-modal-body">
          <button onClick={() => handleAddNodeClick('call-list')}>Звонок на список</button>
          <button onClick={() => handleAddNodeClick('greeting')}>Приветствие</button>
          <button onClick={() => handleAddNodeClick('voice-menu')}>Голосовое меню</button>
          <button onClick={() => handleAddNodeClick('schedule')}>График работы</button>
        </main>
      </div>
    </div>
  );
};

export default AddNodeModal; 