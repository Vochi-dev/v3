import React from 'react';
import './ExternalNumberModal.css';

interface ExternalNumberModalProps {
  isOpen: boolean;
  onClose: () => void;
  onDelete: () => void;
}

const ExternalNumberModal: React.FC<ExternalNumberModalProps> = ({ 
  isOpen, 
  onClose,
  onDelete
}) => {
    if (!isOpen) {
        return null;
    }

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Выбор внешнего номера</h3>
                    <button onClick={onClose} className="close-button">&times;</button>
                </div>
                <div className="modal-body">
                    <p>Здесь будет функционал выбора внешнего номера. (Заглушка)</p>
                </div>
                <div className="modal-footer">
                    <div className="footer-buttons-left">
                        <button onClick={onDelete} className="delete-button">
                            УДАЛИТЬ
                        </button>
                    </div>
                    <div className="footer-buttons-right">
                        <button className="cancel-button" onClick={onClose}>ОТМЕНА</button>
                        <button className="ok-button" onClick={onClose}>ОК</button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default ExternalNumberModal; 