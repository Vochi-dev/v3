import React, { ReactNode } from 'react';
import './Modal.css'; // Переиспользуем существующие стили

interface GenericModalProps {
  title: string;
  children: ReactNode;
  onClose: () => void;
}

const GenericModal: React.FC<GenericModalProps> = ({ title, children, onClose }) => {
  // Останавливаем всплытие события, чтобы клик внутри модалки не закрывал ее
  const onContentClick = (e: React.MouseEvent) => e.stopPropagation();

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={onContentClick}>
        <div className="modal-header">
          <h2>{title}</h2>
          <button onClick={onClose} className="close-button">&times;</button>
        </div>
        <div className="modal-body">
          {children}
        </div>
      </div>
    </div>
  );
};

export default GenericModal; 