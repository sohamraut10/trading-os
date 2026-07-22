"use client";
import { motion } from "framer-motion";

export default function Orb({color,mode}:{color:string,mode:string}) {
  const speed = mode === "thinking" ? 2.4 : mode === "listening" ? 3.2 : 5;
  return (
    <div className="orbWrap" style={{"--orb":color} as React.CSSProperties}>
      <motion.div className="halo" animate={{scale:[.94,1.08,.94],opacity:[.55,.9,.55]}} transition={{duration:speed,repeat:Infinity}} />
      <motion.div className="dust" animate={{rotate:360}} transition={{duration:28,repeat:Infinity,ease:"linear"}} />
      <motion.div className="ring ringA" animate={{rotate:360}} transition={{duration:18,repeat:Infinity,ease:"linear"}} />
      <motion.div className="ring ringB" animate={{rotate:-360}} transition={{duration:25,repeat:Infinity,ease:"linear"}} />
      <motion.div className="orb" animate={{scale:[1,1.025,1]}} transition={{duration:speed,repeat:Infinity}} />
    </div>
  )
}