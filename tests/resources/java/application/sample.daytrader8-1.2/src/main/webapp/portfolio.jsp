<!-- 
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
-->
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
<HTML>
<HEAD>
<META http-equiv="Content-Style-Type" content="text/css">
<TITLE>DayTrader Portfolio</TITLE>
<LINK rel="stylesheet" href="style.css" type="text/css" />
<link rel="shortcut icon" href="./favicon.ico" />
</HEAD>
<BODY bgcolor="#ffffff" link="#000099" vlink="#000099">
    <%@ page
        import="java.util.Collection, 
                java.util.Iterator, 
                java.util.HashMap, 
                java.math.BigDecimal,com.ibm.websphere.samples.daytrader.entities.HoldingDataBean,com.ibm.websphere.samples.daytrader.entities.OrderDataBean,com.ibm.websphere.samples.daytrader.entities.QuoteDataBean,com.ibm.websphere.samples.daytrader.util.Log,com.ibm.websphere.samples.daytrader.util.FinancialUtils"
        session="true" isThreadSafe="true" isErrorPage="false"%>
    <jsp:useBean id="results" scope="request" type="java.lang.String" />
    <jsp:useBean id="holdingDataBeans" type="java.util.Collection<?>"
        scope="request" />
    <jsp:useBean id="quoteDataBeans" type="java.util.Collection<?>"
        scope="request" />
    <TABLE height="54">
        <TBODY>
            <TR>
                <TD bgcolor="#c93333" align="left" width="640"
                    height="10" colspan=6><B><FONT
                        color="#ffffff">DayTrader Portfolio</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
            <TR align="left">
                <TD><B><A href="app?action=home">Home</A></B></TD>
                <TD><B><A href="app?action=account">Account</A></B></TD>
                <TD><B><A href="app?action=mksummary">Market Summary</A></B></TD>
                <TD><B><A href="app?action=portfolio">Portfolio</A></B></TD>
                <TD><B><A href="app?action=quotes&amp;symbols=s:0,s:1,s:2,s:3,s:4">Quotes/Trade</A></B></TD>
                <TD><B><A href="app?action=logout">Logoff</A></B></TD>
                <TD></TD>
            </TR>
            <TR>
                <TD align="right" colspan="7">
                    <HR> <FONT color="#ff0000" size="-2"><%=new java.util.Date()%></FONT>
                </TD>
            </TR>
            <%
                Collection<?> closedOrders = (Collection<?>) request.getAttribute("closedOrders");
                if ((closedOrders != null) && (closedOrders.size() > 0)) {
            %>
            <TR>
                <TD colspan="6" bgcolor="#ff0000"><BLINK>
                        <B><FONT color="#ffffff">Alert: The
                                following Order(s) have completed.</FONT></B>
                    </BLINK></TD>
            </TR>
            <TR align="center">
                <TD colspan="6">
                    <TABLE border="1" style="font-size: smaller">
                        <TBODY>
                            <%
                                Iterator<?> it = closedOrders.iterator();
                                    while (it.hasNext()) {
                                        OrderDataBean closedOrderData = (OrderDataBean) it.next();
                            %>
                            <TR align="center">
                                <TD><A href="docs/glossary.html">order
                                        ID</A></TD>
                                <TD><A href="docs/glossary.html">order
                                        status</A></TD>
                                <TD><A href="docs/glossary.html">creation
                                        date</A></TD>
                                <TD><A href="docs/glossary.html">completion
                                        date</A></TD>
                                <TD><A href="docs/glossary.html">txn
                                        fee</A></TD>
                                <TD><A href="docs/glossary.html">type</A></TD>
                                <TD><A href="docs/glossary.html">symbol</A></TD>
                                <TD><A href="docs/glossary.html">quantity</A></TD>
                            </TR>
                            <TR align="center">
                                <TD><%=closedOrderData.getOrderID()%></TD>
                                <TD><%=closedOrderData.getOrderStatus()%></TD>
                                <TD><%=closedOrderData.getOpenDate()%></TD>
                                <TD><%=closedOrderData.getCompletionDate()%></TD>
                                <TD><%=closedOrderData.getOrderFee()%></TD>
                                <TD><%=closedOrderData.getOrderType()%></TD>
                                <TD><%=FinancialUtils.printQuoteLink(closedOrderData.getSymbol())%></TD>
                                <TD><%=closedOrderData.getQuantity()%></TD>
                            </TR>
                            <%
                                }
                            %>

                        </TBODY>
                    </TABLE>
                </TD>
            </TR>
            <%
                }
            %>
        </TBODY>
    </TABLE>
    <TABLE width="645">
        <TBODY>
            <TR>
                <TD valign="top" width="643">
                    <TABLE width="100%">
                        <TBODY>

                            <TR>
                                <TD colspan="5" bgcolor="#cccccc"><B>Portfolio
                                </B></TD>
                                <TD bgcolor="#cccccc" align="right"><B>Number
                                        of Holdings: </B><%=holdingDataBeans.size()%></TD>
                            </TR>
                            <TR align="center">
                                <TD colspan="6">
                                    <CENTER></CENTER>
                                    <TABLE border="1"
                                        style="font-size: smaller">
                                        <CAPTION align="bottom">
                                            <B>Portfolio </B>
                                        </CAPTION>
                                        <TBODY>
                                            <TR align="center">
                                                <TD><A
                                                    href="docs/glossary.html">holding
                                                        ID</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">purchase
                                                        date</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">symbol</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">quantity</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">purchase
                                                        price</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">current
                                                        price</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">purchase
                                                        basis</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">market
                                                        value</A></TD>
                                                <TD><A
                                                    href="docs/glossary.html">gain/(loss)</A></TD>
                                                <TD><B><A
                                                        href="docs/glossary.html">trade</A></B></TD>
                                            </TR>
                                            <%
                                                // Create Hashmap for quick lookup of quote values
                                                Iterator<?> it = quoteDataBeans.iterator();
                                                HashMap<Object,Object> quoteMap = new HashMap<Object,Object>();
                                                while (it.hasNext()) {
                                                    QuoteDataBean quoteData = (QuoteDataBean) it.next();
                                                    quoteMap.put(quoteData.getSymbol(), quoteData);
                                                }
                                                //Step through and printout Holdings

                                                it = holdingDataBeans.iterator();
                                                BigDecimal totalGain = new BigDecimal(0.0);
                                                BigDecimal totalBasis = new BigDecimal(0.0);
                                                BigDecimal totalValue = new BigDecimal(0.0);
                                                try {
                                                    while (it.hasNext()) {
                                                        HoldingDataBean holdingData = (HoldingDataBean) it.next();
                                                        QuoteDataBean quoteData = (QuoteDataBean) quoteMap.get(holdingData.getQuoteID());
                                                        BigDecimal basis = holdingData.getPurchasePrice().multiply(new BigDecimal(holdingData.getQuantity()));
                                                        BigDecimal marketValue = quoteData.getPrice().multiply(new BigDecimal(holdingData.getQuantity()));
                                                        totalBasis = totalBasis.add(basis);
                                                        totalValue = totalValue.add(marketValue);
                                                        BigDecimal gain = marketValue.subtract(basis);
                                                        totalGain = totalGain.add(gain);
                                                        BigDecimal gainPercent = null;
                                                        if (basis.doubleValue() == 0.0) {
                                                            gainPercent = new BigDecimal(0.0);
                                                            Log.error("portfolio.jsp: Holding with zero basis. holdingID=" + holdingData.getHoldingID() + " symbol=" + holdingData.getQuoteID()
                                                                    + " purchasePrice=" + holdingData.getPurchasePrice());
                                                        } else
                                                            gainPercent = marketValue.divide(basis, BigDecimal.ROUND_HALF_UP).subtract(new BigDecimal(1.0)).multiply(new BigDecimal(100.0));
                                            %>
                                            <TR bgcolor="#fafcb6"
                                                align="center">
                                                <TD><%=holdingData.getHoldingID()%></TD>
                                                <TD><%=holdingData.getPurchaseDate()%></TD>
                                                <TD><%=FinancialUtils.printQuoteLink(holdingData.getQuoteID())%></TD>
                                                <TD><%=holdingData.getQuantity()%></TD>
                                                <TD><%=holdingData.getPurchasePrice()%></TD>
                                                <TD><%=quoteData.getPrice()%></TD>
                                                <TD><%=basis%></TD>
                                                <TD><%=marketValue%></TD>
                                                <TD><%=FinancialUtils.printGainHTML(gain)%></TD>
                                                <TD><B><%="<A href=\"app?action=sell&holdingID=" + holdingData.getHoldingID() + "\">sell</A>"%></B></TD>
                                            </TR>
                                            <%
                                                }
                                                } catch (Exception e) {
                                                    Log.error("portfolio.jsp: error displaying user holdings", e);
                                                }
                                            %>
                                            <TR align="center">
                                                <TD></TD>
                                                <TD></TD>
                                                <TD></TD>
                                                <TD></TD>
                                                <TD></TD>
                                                <TD><B>Total</B></TD>
                                                <TD align="center">$
                                                    <%=totalBasis%></TD>
                                                <TD align="center">$
                                                    <%=totalValue%></TD>
                                                <TD align="center"
                                                    colspan="2">$ <%=FinancialUtils.printGainHTML(totalGain)%>
                                                    <%=FinancialUtils.printGainPercentHTML(FinancialUtils.computeGainPercent(totalValue, totalBasis))%></TD>
                                            </TR>
                                        </TBODY>
                                    </TABLE>
                                    <CENTER></CENTER>
                                </TD>
                            </TR>
                            <TR>
                                <TD colspan="6"></TD>
                            </TR>
                        </TBODY>
                    </TABLE>
                </TD>
            </TR>
        </TBODY>
    </TABLE>
    <TABLE height="54" style="font-size: smaller">
        <TBODY>
            <TR>
                <TD colspan="2">
                    <HR>
                </TD>
            </TR>
            <TR>
                <TD colspan="2">
                    <TABLE width="100%" style="font-size: smaller">
                        <TBODY>
                            <TR>
                                <TD>Note: Click any <A
                                    href="docs/glossary.html">symbol</A>
                                    for a quote or to trade.
                                </TD>
                                <TD align="right"><FORM>
                                        <INPUT type="submit"
                                            name="action" value="quotes">
                                        <INPUT size="20" type="text"
                                            name="symbols"
                                            value="s:0, s:1, s:2, s:3, s:4">
                                    </FORM></TD>
                            </TR>
                        </TBODY>
                    </TABLE>
                </TD>
            </TR>
            <TR>
                <TD bgcolor="#c93333" align="left" width="640"
                    height="10"><B><FONT color="#ffffff">DayTrader
                            Portfolio</FONT></B></TD>
                <TD align="center" bgcolor="#000000" width="100"
                    height="10"><FONT color="#ffffff"><B>DayTrader</B></FONT></TD>
            </TR>
        </TBODY>
    </TABLE>
</BODY>
</HTML>
