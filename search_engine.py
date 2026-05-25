class AliExpressSearchEngine:
    # 1. Filtro de Rejeição (Blacklist)
    BLACKLIST = [
        "clothes", "dress", "sexy", "lingerie", "toy", "plush", "poster", "sticker", 
        "baby", "cosplay", "t-shirt", "jewelry", "makeup"
    ]
    
    # 2. Palavras obrigatórias de Nicho (Tech/Informática)
    NICHE_KEYWORDS = [
        "ssd", "keyboard", "mouse", "monitor", "router", "hub", "pc", "gaming", 
        "usb", "headset", "ram", "ddr4", "ddr5", "nvme", "gpu"
    ]

    def _is_relevant(self, title: str) -> bool:
        t = title.lower()
        
        # Se contiver algo da blacklist, descarta imediatamente
        if any(bad in t for bad in self.BLACKLIST):
            return False
            
        # Garante que o título contenha pelo menos uma palavra do seu nicho
        if not any(good in t for good in self.NICHE_KEYWORDS):
            return False
            
        return True

    # Dentro da sua função que processa os produtos (ex: dentro do loop de inserção):
    def process_product(self, prod):
        titulo = prod.get("product_title", "")
        
        if not self._is_relevant(titulo):
            logger.info(f"Filtro: Produto rejeitado (fora do nicho): {titulo[:50]}...")
            return None # Ignora o produto
            
        # Se passar no filtro, segue com a lógica de payload e upsert...
