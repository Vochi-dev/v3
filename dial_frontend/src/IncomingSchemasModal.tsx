import React from 'react';
import './Modal.css'; // Используем общие стили для модальных окон

interface IncomingSchemasModalProps {
    enterpriseId: string;
    onClose: () => void;
}

const IncomingSchemasModal: React.FC<IncomingSchemasModalProps> = ({ enterpriseId, onClose }) => {
    return (
        <div className="modal-backdrop">
            <div className="modal-content">
                <div className="modal-header">
                    <h2>Входящие схемы для предприятия: {enterpriseId}</h2>
                    <button onClick={onClose} className="modal-close-button">&times;</button>
                </div>
                <div className="modal-body">
                    <p>Список входящих схем пока пуст.</p>
                </div>
                <div className="modal-footer">
                     <button className="add-schema-button">Добавить новую схему</button>
                </div>
            </div>
        </div>
    );
};

export default IncomingSchemasModal; 