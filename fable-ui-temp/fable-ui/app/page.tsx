"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import Orb from "@/components/Orb";

const states = {
  idle: { color: "#5B8CFF", label: "IDLE", line: "How can I help with your money today?" },
  listening: { color: "#38E1FF", label: "LISTENING", line: "I'm listening..." },
  thinking: { color: "#8A5CFF", label: "THINKING", line: "Working through your financial picture..." },
  speaking: { color: "#D96BFF", label: "SPEAKING", line: "Here’s what I found." }
} as const;

type State = keyof typeof states;

export default function Home() {
  const [mode, setMode] = useState<State>("idle");
  const s = states[mode];

  const cycle = () => {
    const order: State[] = ["idle", "listening", "thinking", "speaking"];
    setMode(order[(order.indexOf(mode) + 1) % order.length]);
  };

  return (
    <main className="shell" style={{"--accent": s.color} as React.CSSProperties}>
      <div className="grain" />
      <section className="mobile">
        <header>
          <button className="icon">⌁</button>
          <div><div className="wordmark">FABLE</div><div className="tag">YOUR FINANCIAL INTELLIGENCE</div></div>
          <button className="icon">✧</button>
        </header>

        <div className="hero">
          <Orb color={s.color} mode={mode} />
          <motion.div key={mode} initial={{opacity:0,y:5}} animate={{opacity:1,y:0}} className="status">
            <span className="dot" /> {s.label}
          </motion.div>
          <div className="prompt">{s.line}</div>
          <div className="wave">{Array.from({length:29}).map((_,i)=><i key={i} style={{height:`${5 + (i%7)*3}px`}} />)}</div>
        </div>

        <footer>
          <button className="icon">↶</button>
          <button className="talk" onClick={cycle}><span className="mic">♩</span>{mode === "listening" ? "RELEASE TO SEND" : "HOLD TO TALK"}</button>
          <button className="icon">⠿</button>
        </footer>
        <div className="market"><span /> MARKET STATUS <b>● OPEN</b></div>
      </section>

      <section className="desktop">
        <div className="deskTop"><div className="wordmark">FABLE</div><div className="live">— {s.label} &nbsp;⌁⌁⌁&nbsp; {s.label} —</div><div className="tools">◌ &nbsp; ⚙ &nbsp; ◎</div></div>
        <aside className="left">
          <Panel title="PORTFOLIO OVERVIEW"><small>TOTAL VALUE</small><strong>₹24,78,632 <em>+1.62%</em></strong><div className="chart">⌁⌁╱⌁╱╲╱╲╱╲╱</div><Rows /></Panel>
          <Panel title="UPCOMING EVENTS"><p>SIP · HDFC Top 100<br/><small>In 2 days</small></p><p>Credit Card Bill<br/><small>In 5 days</small></p></Panel>
        </aside>
        <div className="deskHero"><Orb color={s.color} mode={mode}/><h2>{s.line}</h2><p>Ask anything about your finances</p><button className="roundMic" onClick={cycle}>♩</button></div>
        <aside className="right">
          <Panel title="MARKET PULSE"><p><b>NIFTY 50</b><br/>24,854.15 <em>+0.75%</em></p><p>SENSEX<br/>81,330.56 <em>+0.42%</em></p><p>USD / INR<br/>83.47 <i>-0.12%</i></p></Panel>
          <Panel title="FABLE INSIGHT"><p>Your portfolio is well diversified. Consider increasing exposure to hybrid funds for optimal balance.</p></Panel>
          <Panel title="RECENT ACTIVITY"><p>ICICI Bank &nbsp; -₹2,450</p><p>SIP · Parag Parikh &nbsp; -₹10,000</p><p>Dividend · TCS &nbsp; <em>+₹3,150</em></p></Panel>
        </aside>
      </section>
    </main>
  );
}

function Panel({title,children}:{title:string,children:React.ReactNode}) {
  return <div className="panel"><h3><span/> {title}</h3>{children}</div>
}
function Rows() {
  return <div className="rows"><p>◈ EQUITIES <b>₹15,64,982</b></p><p>◉ MUTUAL FUNDS <b>₹6,21,430</b></p><p>₿ CRYPTO <b>₹1,45,230</b></p><p>▣ CASH <b>₹1,47,990</b></p></div>
}