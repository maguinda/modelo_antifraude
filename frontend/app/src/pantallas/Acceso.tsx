import { useState, type FormEvent } from "react";

interface Props {
  mensaje?: string;
  alEntrar: (clave: string) => void;
}

export function Acceso({ mensaje, alEntrar }: Props) {
  const [clave, setClave] = useState("");
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (clave.trim()) alEntrar(clave.trim());
  };
  return (
    <section className="tarjeta acceso" aria-labelledby="tituloAcceso">
      <h2 id="tituloAcceso">Entrar</h2>
      <p>Escribe tu clave de analista. Solo se guarda mientras esta pestaña esté abierta.</p>
      <form onSubmit={enviar} noValidate autoComplete="off">
        <div className="campo">
          <label htmlFor="clave">Clave de analista</label>
          <input id="clave" type="password" autoComplete="off" value={clave} onChange={(e) => setClave(e.target.value)} />
        </div>
        <button type="submit" className="btn azul" disabled={!clave.trim()}>
          Entrar
        </button>
      </form>
      {mensaje ? (
        <p className="error" role="alert">
          {mensaje}
        </p>
      ) : null}
    </section>
  );
}
