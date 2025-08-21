/**
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
 */
package com.ibm.websphere.samples.daytrader.web.prims.ejb3;

import java.io.IOException;
import java.util.Collection;
import java.util.Iterator;

import javax.ejb.EJB;
import javax.inject.Inject;
import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.daytrader.entities.HoldingDataBean;
import com.ibm.websphere.samples.daytrader.impl.ejb3.TradeSLSBBean;
import com.ibm.websphere.samples.daytrader.interfaces.TradeEJB;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;

/**
 *
 * PingServlet2Session2Entity tests key functionality of a servlet call to a
 * stateless SessionEJB, and then to a Entity EJB representing data in a
 * database. This servlet makes use of the Stateless Session EJB {@link Trade},
 * and then uses {@link TradeConfig} to generate a random user. The users
 * portfolio is looked up using the Holding Entity EJB returnin a collection of
 * Holdings
 *
 */
@WebServlet(name = "ejb3.PingServlet2Session2EntityCollection", urlPatterns = { "/ejb3/PingServlet2Session2EntityCollection" })
public class PingServlet2Session2EntityCollection extends HttpServlet {

    private static final long serialVersionUID = 6171380014749902308L;

    private static String initTime;

    private static int hitCount;

    @Inject
    @TradeEJB
     private TradeServices tradeSLSBLocal;
    
    @Override
    public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        doGet(req, res);
    }

    @Override
    public void doGet(HttpServletRequest req, HttpServletResponse res) throws IOException, ServletException {

        res.setContentType("text/html");
        java.io.PrintWriter out = res.getWriter();
        String userID = null;
        Collection<?> holdingDataBeans = null;
        StringBuffer output = new StringBuffer(100);

        output.append("<html><head><title>PingServlet2Session2EntityCollection</title></head>"
                + "<body><HR><FONT size=\"+2\" color=\"#000066\">PingServlet2Session2EntityCollection<BR></FONT>" + "<FONT size=\"-1\" color=\"#000066\">"
                + "PingServlet2Session2EntityCollection tests the common path of a Servlet calling a Session EJB "
                + "which in turn calls a finder on an Entity EJB returning a collection of Entity EJBs.<BR>");

        try {

            try {
                int iter = TradeConfig.getPrimIterations();
                for (int ii = 0; ii < iter; ii++) {
                    userID = TradeConfig.rndUserID();
                    // getQuote will call findQuote which will instaniate the
                    // Quote Entity Bean
                    // and then will return a QuoteObject
                    holdingDataBeans = tradeSLSBLocal.getHoldings(userID);
                    // trade.remove();
                }
            } catch (Exception ne) {
                Log.error(ne, "PingServlet2Session2EntityCollection.goGet(...): exception getting HoldingData collection through Trade for user " + userID);
                throw ne;
            }

            output.append("<HR>initTime: " + initTime).append("<BR>Hit Count: " + hitCount++);
            output.append("<HR>User: " + userID + " is currently holding " + holdingDataBeans.size() + " stock holdings:");
            Iterator<?> it = holdingDataBeans.iterator();
            while (it.hasNext()) {
                HoldingDataBean holdingData = (HoldingDataBean) it.next();
                output.append("<BR>" + holdingData.toHTML());
            }
            out.println(output.toString());

        } catch (Exception e) {
            Log.error(e, "PingServlet2Session2EntityCollection.doGet(...): General Exception caught");
            res.sendError(500, "General Exception caught, " + e.toString());
        }
    }

    @Override
    public String getServletInfo() {
        return "web primitive, tests Servlet to Session to Entity returning a collection of Entity EJBs";
    }

    @Override
    public void init(ServletConfig config) throws ServletException {
        super.init(config);
        hitCount = 0;
        initTime = new java.util.Date().toString();
    }
}