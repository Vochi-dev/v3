import React from 'react';
import './Modal.css'; // Общие стили для модальных окон.

interface OutgoingSchemasModalProps {
    enterpriseId: string;
    onClose: () => void;
}

const OutgoingSchemasModal: React.FC<OutgoingSchemasModalProps> = ({ enterpriseId, onClose }) => {
    return (
        <div className="modal-backdrop">
            <div className="modal-content" style={{ maxWidth: '800px' }}>
                <div className="modal-header">
                    <h2>Исходящие схемы для предприятия: {enterpriseId}</h2>
                    <button onClick={onClose} className="modal-close-button">&times;</button>
                </div>
                <div className="modal-body">
                    {/* Контент для исходящих схем будет здесь */}
                    <p>Функционал исходящих схем находится в разработке.</p>
                </div>
                <div className="modal-footer">
                    <button 
                        className="add-schema-button" 
                        onClick={() => alert('Добавление новой исходящей схемы в разработке')}
                        style={{
                            backgroundColor: '#007bff',
                            color: 'white',
                            border: 'none',
                            padding: '10px 20px',
                            borderRadius: '5px',
                            cursor: 'pointer'
                        }}
                    >
                        Добавить новую схему
                    </button>
                </div>
            </div>
        </div>
    );
};

export default OutgoingSchemasModal; 