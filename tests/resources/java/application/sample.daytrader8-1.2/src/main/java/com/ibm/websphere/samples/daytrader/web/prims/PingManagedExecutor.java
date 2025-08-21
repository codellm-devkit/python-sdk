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
package com.ibm.websphere.samples.daytrader.web.prims;

import java.io.IOException;

import javax.annotation.Resource;
import javax.enterprise.concurrent.ManagedExecutorService;
import javax.servlet.AsyncContext;
import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.ServletOutputStream;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

@WebServlet(asyncSupported=true,name = "PingManagedExecutor", urlPatterns = { "/servlet/PingManagedExecutor" })
public class PingManagedExecutor extends HttpServlet{

	private static final long serialVersionUID = -4695386150928451234L;
	private static String initTime;
    private static int hitCount;

	@Resource 
	private ManagedExecutorService mes;
	
	 /**
     * forwards post requests to the doGet method Creation date: (03/18/2014
     * 10:52:39 AM)
     *
     * @param res
     *            javax.servlet.http.HttpServletRequest
     * @param res2
     *            javax.servlet.http.HttpServletResponse
     */
    @Override
    public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        doGet(req, res);
    }

    /**
     * this is the main method of the servlet that will service all get
     * requests.
     *
     * @param request
     *            HttpServletRequest
     * @param responce
     *            HttpServletResponce
     **/
    @Override
	protected void doGet(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {

    	final AsyncContext asyncContext = req.startAsync();
        final ServletOutputStream out = res.getOutputStream();
    	
    	try {
    		res.setContentType("text/html");
    		    		
    		out.println("<html><head><title>Ping ManagedExecutor</title></head>"
                    + "<body><HR><BR><FONT size=\"+2\" color=\"#000066\">Ping ManagedExecutor<BR></FONT><FONT size=\"+1\" color=\"#000066\">Init time : " + initTime
                    + "<BR><BR></FONT>  </body></html>");
    		   		   		    	
    		// Runnable task
    		mes.submit(new Runnable() {
    			@Override
    			public void run() {
    				try {
						out.println("<b>HitCount: " + ++hitCount  +"</b><br/>");
					} catch (IOException e) {
						e.printStackTrace();
					}
    				asyncContext.complete();
    			}
    		});   		    		
    		
    			 
    	} catch (Exception e) {
			e.printStackTrace();
		}  
    }	
    		
    	
    /**
     * returns a string of information about the servlet
     *
     * @return info String: contains info about the servlet
     **/
    @Override
    public String getServletInfo() {
        return "Tests a ManagedExecutor";
    }

    /**
     * called when the class is loaded to initialize the servlet
     *
     * @param config
     *            ServletConfig:
     **/
    @Override
    public void init(ServletConfig config) throws ServletException {
        super.init(config);
        initTime = new java.util.Date().toString();
        hitCount = 0;
    }
	
}
