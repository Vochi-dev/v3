import React, { useState, useEffect } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

interface Schema {
  id: string;
  name: string;
}

const SchemaList: React.FC = () => {
  const [schemas, setSchemas] = useState<Schema[]>([]);
  const { enterpriseId } = useParams<{ enterpriseId: string }>();
  const navigate = useNavigate();

  useEffect(() => {
    if (enterpriseId) {
      fetch(`/dial/api/enterprises/${enterpriseId}/schemas`)
        .then((res) => {
          if (!res.ok) {
            throw new Error('Network response was not ok');
          }
          return res.json();
        })
        .then((data) => setSchemas(data))
        .catch((error) => console.error("Failed to fetch schemas:", error));
    }
  }, [enterpriseId]);

  const handleCreateSchema = () => {
    const schemaName = prompt('Введите имя новой схемы:');
    if (schemaName && enterpriseId) {
      const newSchema = {
        name: schemaName,
        nodes: [
          {
            id: '1',
            type: 'custom',
            data: { label: 'Поступил новый звонок' },
            position: { x: 250, y: 5 },
            draggable: false,
            deletable: false,
          },
        ],
        edges: [],
      };

      fetch(`/dial/api/enterprises/${enterpriseId}/schemas`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(newSchema),
      })
        .then((res) => res.json())
        .then((createdSchema) => {
          navigate(`/enterprise/${enterpriseId}/schema/${createdSchema.id}`);
        })
        .catch(error => console.error("Failed to create schema:", error));
    }
  };

  return (
    <div className="schema-list-container">
      <h1>Схемы для предприятия: {enterpriseId}</h1>
      <button onClick={handleCreateSchema}>Создать новую схему</button>
      <ul>
        {schemas.map((schema) => (
          <li key={schema.id}>
            <Link to={`/enterprise/${enterpriseId}/schema/${schema.id}`}>{schema.name}</Link>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default SchemaList; 